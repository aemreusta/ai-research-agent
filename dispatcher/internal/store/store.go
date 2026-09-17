// Package store is the dispatcher's view of Postgres: claim, count, watch, and settle runs,
// and write its decisions onto the shared run timeline.
//
// Alembic (Python) owns the schema; this package only issues DML. Every status change is a
// compare-and-set on the status - and, for heartbeat decisions, on the heartbeat the watchdog
// actually observed - because the agent writes the same rows concurrently. The dispatcher never
// selects from run_secrets; it only deletes from it when it settles a run.
package store

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	c "github.com/aemreusta/apilex-test-case/dispatcher/internal/contracts"
	"github.com/aemreusta/apilex-test-case/dispatcher/internal/watchdog"
)

// Notification channels, identical to research_agent/observability/events.py.
const (
	QueueChannel  = "run_queued"
	EventsChannel = "run_events"
)

// ErrLostRace means the row changed between observation and write; the caller does nothing.
var ErrLostRace = errors.New("run changed concurrently")

// Store wraps a connection pool.
type Store struct {
	pool    *pgxpool.Pool
	machine c.StateMachine
}

// New connects and checks the database answers.
func New(ctx context.Context, dsn string, machine c.StateMachine) (*Store, error) {
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("connect: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("ping: %w", err)
	}
	return &Store{pool: pool, machine: machine}, nil
}

// Close releases the pool.
func (s *Store) Close() { s.pool.Close() }

// Pool is exposed for the LISTEN loop, which needs a dedicated connection.
func (s *Store) Pool() *pgxpool.Pool { return s.pool }

// Claimed is what dispatch needs to know about a freshly claimed run.
type Claimed struct {
	ID         string
	LeaseID    string
	Attempts   int
	DeadlineAt time.Time
}

// The claim. The deadline is fixed on the first claim and kept across requeues - it bounds the
// run, not the attempt. A run that enables its own wall-clock budget gets that plus a margin,
// so the agent's graceful stop always comes before this hard one.
const claimSQL = `
UPDATE runs
   SET status = 'dispatched',
       dispatched_at = now(),
       attempts = attempts + 1,
       lease_id = gen_random_uuid(),
       agent_id = NULL,
       deadline_at = COALESCE(
           deadline_at,
           now() + make_interval(secs => GREATEST(
               $1::int,
               COALESCE((config_snapshot->'budget'->>'max_wall_clock_seconds')::int + $2::int, 0)
           ))
       )
 WHERE id = (
        SELECT id FROM runs
         WHERE status = 'queued'
           AND (available_at IS NULL OR available_at <= now())
         ORDER BY created_at
           FOR UPDATE SKIP LOCKED
         LIMIT 1
       )
RETURNING id::text, attempts, deadline_at, lease_id::text`

// Claim takes the oldest claimable run, or returns (nil, nil) when there is none.
func (s *Store) Claim(ctx context.Context, hardDeadline, wallClockMargin time.Duration) (*Claimed, error) {
	var claimed Claimed
	err := s.pool.QueryRow(ctx, claimSQL,
		int(hardDeadline.Seconds()), int(wallClockMargin.Seconds()),
	).Scan(&claimed.ID, &claimed.Attempts, &claimed.DeadlineAt, &claimed.LeaseID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("claim: %w", err)
	}
	if err := s.notifyStatus(ctx, s.pool, claimed.ID, c.Dispatched); err != nil {
		return nil, err
	}
	return &claimed, nil
}

// ActiveCount is the number of runs holding a slot somewhere.
func (s *Store) ActiveCount(ctx context.Context) (int, error) {
	var n int
	err := s.pool.QueryRow(ctx,
		`SELECT count(*) FROM runs WHERE status IN ('dispatched', 'running') AND agent_id IS DISTINCT FROM 'cli'`).Scan(&n)
	return n, err
}

// QueuedCount lets the loop log "deferred" only when there is actually something waiting.
func (s *Store) QueuedCount(ctx context.Context) (int, error) {
	var n int
	err := s.pool.QueryRow(ctx, `SELECT count(*) FROM runs WHERE status = 'queued'`).Scan(&n)
	return n, err
}

// Watchlist returns every run the watchdog may need to act on.
func (s *Store) Watchlist(ctx context.Context) ([]watchdog.Run, error) {
	rows, err := s.pool.Query(ctx, `
SELECT id::text, status, attempts, cancel_requested, dispatched_at, heartbeat_at, deadline_at,
       COALESCE(agent_id, ''), COALESCE(lease_id::text, '')
  FROM runs
 WHERE (status IN ('dispatched', 'running')
    OR (status = 'queued' AND deadline_at IS NOT NULL))
   AND agent_id IS DISTINCT FROM 'cli'`)
	if err != nil {
		return nil, fmt.Errorf("watchlist: %w", err)
	}
	defer rows.Close()
	var out []watchdog.Run
	for rows.Next() {
		var run watchdog.Run
		var status string
		if err := rows.Scan(&run.ID, &status, &run.Attempts, &run.CancelRequested,
			&run.DispatchedAt, &run.HeartbeatAt, &run.DeadlineAt, &run.AgentID, &run.LeaseID); err != nil {
			return nil, fmt.Errorf("watchlist scan: %w", err)
		}
		run.Status = c.Status(status)
		out = append(out, run)
	}
	return out, rows.Err()
}

// Requeue sends a run back to the queue, not claimable before backoff has passed.
func (s *Store) Requeue(ctx context.Context, observed watchdog.Run, backoff time.Duration) error {
	return s.transition(ctx, observed, c.Queued, true, `
		agent_id = NULL, lease_id = NULL, heartbeat_at = NULL, dispatched_at = NULL,
		available_at = now() + make_interval(secs => $4::int)`,
		int(backoff.Seconds()))
}

// ReturnUnstarted undoes a claim no replica accepted. The attempt is given back: nothing ran,
// and a briefly saturated cluster must not burn a run's retry budget.
func (s *Store) ReturnUnstarted(ctx context.Context, runID, leaseID string, backoff time.Duration) error {
	tag, err := s.pool.Exec(ctx, `
UPDATE runs
   SET status = 'queued', dispatched_at = NULL, lease_id = NULL, attempts = GREATEST(attempts - 1, 0),
       available_at = now() + make_interval(secs => $2::int)
 WHERE id = $1 AND status = 'dispatched' AND lease_id::text = $3`, runID, int(backoff.Seconds()), leaseID)
	if err != nil {
		return fmt.Errorf("return unstarted: %w", err)
	}
	if tag.RowsAffected() != 1 {
		return ErrLostRace
	}
	return s.notifyStatus(ctx, s.pool, runID, c.Queued)
}

// Fail settles a run as failed and destroys its provider keys.
func (s *Store) Fail(ctx context.Context, observed watchdog.Run, code, reason string) error {
	return s.transition(ctx, observed, c.Failed, code != c.CodeDeadlineExceeded,
		`error_code = $4, error_message = $5, finished_at = now()`, code, reason)
}

// Cancel settles a run as cancelled and destroys its provider keys.
func (s *Store) Cancel(ctx context.Context, observed watchdog.Run, reason string) error {
	return s.transition(ctx, observed, c.Cancelled, true,
		`stop_reason = 'cancelled', error_message = $4, finished_at = now()`, reason)
}

// RequestCancel sets the flag the agent polls; it is the source of truth for cancellation.
func (s *Store) RequestCancel(ctx context.Context, runID string) error {
	_, err := s.pool.Exec(ctx, `UPDATE runs SET cancel_requested = true WHERE id = $1`, runID)
	return err
}

// transition is the compare-and-set every settle goes through. $1 = id, $2 = observed status,
// $3 = observed heartbeat; extra arguments start at $4.
func (s *Store) transition(ctx context.Context, observed watchdog.Run, target c.Status, checkHeartbeat bool, set string, extra ...any) error {
	if !s.machine.CanTransition(observed.Status, target) {
		return fmt.Errorf("contract forbids %s -> %s", observed.Status, target)
	}
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	args := append([]any{observed.ID, string(observed.Status), observed.HeartbeatAt}, extra...)
	args = append(args, observed.LeaseID, checkHeartbeat)
	query := fmt.Sprintf(`
UPDATE runs SET status = '%s', %s
 WHERE id = $1 AND status = $2
   AND (NOT $%d::boolean OR heartbeat_at IS NOT DISTINCT FROM $3::timestamptz)
   AND COALESCE(lease_id::text, '') = $%d`, target, set, len(args), len(args)-1)
	tag, err := tx.Exec(ctx, query, args...)
	if err != nil {
		return fmt.Errorf("%s -> %s: %w", observed.Status, target, err)
	}
	if tag.RowsAffected() != 1 {
		return ErrLostRace
	}
	if s.machine.IsTerminal(target) {
		// A terminal observer must see the final event in the same commit as the status.
		var code, reason string
		if err := tx.QueryRow(ctx, `SELECT COALESCE(error_code, ''), COALESCE(error_message, '') FROM runs WHERE id = $1`, observed.ID).Scan(&code, &reason); err != nil {
			return err
		}
		message := "Run finished: " + string(target) + ". " + reason
		data := map[string]any{"status": string(target), "display": "[Dispatcher] " + message, "error_code": code, "reason": reason}
		var seq int
		if err := tx.QueryRow(ctx, insertEventSQL, observed.ID, "warn", "run_finished",
			message, mustJSON(data), code).Scan(&seq); err != nil {
			return fmt.Errorf("terminal event: %w", err)
		}
		if _, err := tx.Exec(ctx, `DELETE FROM run_secrets WHERE run_id = $1`, observed.ID); err != nil {
			return fmt.Errorf("delete secrets: %w", err)
		}
	}
	if target == c.Queued {
		if _, err := tx.Exec(ctx, `SELECT pg_notify($1, $2)`, QueueChannel,
			mustJSON(map[string]string{"run_id": observed.ID})); err != nil {
			return err
		}
	}
	if err := s.notifyStatus(ctx, tx, observed.ID, target); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

type querier interface {
	QueryRow(ctx context.Context, sql string, args ...any) pgx.Row
}

func (s *Store) notifyStatus(ctx context.Context, q querier, runID string, status c.Status) error {
	var ignored string
	payload := mustJSON(map[string]string{"run_id": runID, "status": string(status)})
	if err := q.QueryRow(ctx, `SELECT pg_notify($1, $2)::text`, EventsChannel, payload).Scan(&ignored); err != nil {
		return fmt.Errorf("notify: %w", err)
	}
	return nil
}

// Event is one dispatcher entry on the run timeline.
type Event struct {
	RunID     string
	Level     string // info | warn | error
	Type      string // see EventType in events.py
	Message   string
	Data      map[string]any
	ErrorCode string
}

// The same statement as the Python EventWriter: the per-run counter makes seq gap-free and
// shared between the two writers.
const insertEventSQL = `
WITH next AS (
    UPDATE runs SET event_seq = event_seq + 1 WHERE id = $1 RETURNING event_seq
)
INSERT INTO run_events (run_id, seq, level, node, event_type, message, data, error_code, expected)
SELECT $1, next.event_seq, $2, 'dispatcher', $3, $4, $5::jsonb, NULLIF($6, ''),
       CASE WHEN $6 = '' THEN NULL ELSE true END
  FROM next
RETURNING seq`

// Emit writes one event and wakes SSE listeners.
func (s *Store) Emit(ctx context.Context, event Event) error {
	data := event.Data
	if data == nil {
		data = map[string]any{}
	}
	data["display"] = "[Dispatcher] " + event.Message
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	var seq int
	if err := tx.QueryRow(ctx, insertEventSQL, event.RunID, event.Level, event.Type,
		event.Message, mustJSON(data), event.ErrorCode).Scan(&seq); err != nil {
		return fmt.Errorf("insert event: %w", err)
	}
	if _, err := tx.Exec(ctx, `SELECT pg_notify($1, $2)`, EventsChannel,
		mustJSON(map[string]any{"run_id": event.RunID, "seq": seq})); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func mustJSON(value any) string {
	encoded, err := json.Marshal(value)
	if err != nil {
		panic(err) // only ever called with maps of plain values
	}
	return string(encoded)
}
