package store

// Integration tests against real Postgres with the Alembic schema applied. They skip unless
// TEST_DATABASE_URL is set (the Python suite migrates the same database).

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"sync"
	"testing"
	"time"

	c "github.com/aemreusta/apilex-test-case/dispatcher/internal/contracts"
	"github.com/aemreusta/apilex-test-case/dispatcher/internal/watchdog"
)

func open(t *testing.T) *Store {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("set TEST_DATABASE_URL to run store integration tests")
	}
	_, file, _, _ := runtime.Caller(0)
	contracts, err := c.Load(filepath.Join(filepath.Dir(file), "..", "..", "..", "contracts"))
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	s, err := New(ctx, dsn, contracts.States)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Close)
	if _, err := s.pool.Exec(ctx, `TRUNCATE runs CASCADE`); err != nil {
		t.Fatalf("truncate (is the schema migrated?): %v", err)
	}
	return s
}

func insertRun(t *testing.T, s *Store, snapshot string) string {
	t.Helper()
	if snapshot == "" {
		snapshot = `{"budget": {"max_wall_clock_seconds": null}}`
	}
	var id string
	err := s.pool.QueryRow(context.Background(), `
INSERT INTO runs (id, status, question_masked, config_snapshot, config_hash, overrides,
                  prompt_versions, models_used, skills_used, key_sources, iteration,
                  searches_used, tokens_in, tokens_out, cost_usd, event_seq, attempts,
                  cancel_requested)
VALUES (gen_random_uuid(), 'queued', 'q', $1::jsonb, repeat('a', 64), '{}', '{}', '{}', '[]',
        '{}', 0, 0, 0, 0, 0, 0, 0, false)
RETURNING id::text`, snapshot).Scan(&id)
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(2 * time.Millisecond) // distinct created_at for ordering
	return id
}

func snapshot(t *testing.T, s *Store, id string) watchdog.Run {
	t.Helper()
	runs, err := s.Watchlist(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	for _, run := range runs {
		if run.ID == id {
			return run
		}
	}
	t.Fatalf("run %s not on the watchlist", id)
	return watchdog.Run{}
}

func markRunning(t *testing.T, s *Store, id string, heartbeatAgo time.Duration) {
	t.Helper()
	_, err := s.pool.Exec(context.Background(), `
UPDATE runs SET status = 'running', agent_id = 'a1', started_at = now(),
       heartbeat_at = now() - make_interval(secs => $2::int)
 WHERE id = $1`, id, int(heartbeatAgo.Seconds()))
	if err != nil {
		t.Fatal(err)
	}
}

func TestConcurrentDispatchersNeverClaimTheSameRun(t *testing.T) {
	s := open(t)
	want := map[string]bool{}
	for range 8 {
		want[insertRun(t, s, "")] = true
	}

	var mu sync.Mutex
	var got []string
	var wg sync.WaitGroup
	for range 4 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				claimed, err := s.Claim(context.Background(), time.Hour, time.Minute)
				if err != nil {
					t.Error(err)
					return
				}
				if claimed == nil {
					return
				}
				mu.Lock()
				got = append(got, claimed.ID)
				mu.Unlock()
			}
		}()
	}
	wg.Wait()

	sort.Strings(got)
	if len(got) != len(want) {
		t.Fatalf("claimed %d runs, want %d: %v", len(got), len(want), got)
	}
	for i := 1; i < len(got); i++ {
		if got[i] == got[i-1] {
			t.Fatalf("run %s claimed twice", got[i])
		}
	}
}

func TestClaimFixesTheHardDeadlineOnce(t *testing.T) {
	s := open(t)
	id := insertRun(t, s, "")
	claimed, err := s.Claim(context.Background(), 30*time.Minute, time.Minute)
	if err != nil || claimed == nil {
		t.Fatal(err)
	}
	if d := time.Until(claimed.DeadlineAt); d < 29*time.Minute || d > 31*time.Minute {
		t.Fatalf("deadline in %s, want ~30m", d)
	}

	// A requeued run keeps its original deadline: it bounds the run, not the attempt.
	markRunning(t, s, id, time.Hour)
	if err := s.Requeue(context.Background(), snapshot(t, s, id), 0); err != nil {
		t.Fatal(err)
	}
	again, err := s.Claim(context.Background(), 2*time.Hour, time.Minute)
	if err != nil || again == nil {
		t.Fatal(err)
	}
	if !again.DeadlineAt.Equal(claimed.DeadlineAt) {
		t.Fatalf("deadline moved from %s to %s", claimed.DeadlineAt, again.DeadlineAt)
	}
	if again.Attempts != 2 {
		t.Fatalf("attempts = %d", again.Attempts)
	}
}

func TestAWallClockBudgetExtendsTheDeadline(t *testing.T) {
	s := open(t)
	insertRun(t, s, `{"budget": {"max_wall_clock_seconds": 7200}}`)
	claimed, err := s.Claim(context.Background(), 30*time.Minute, time.Minute)
	if err != nil || claimed == nil {
		t.Fatal(err)
	}
	if d := time.Until(claimed.DeadlineAt); d < 2*time.Hour || d > 2*time.Hour+2*time.Minute {
		t.Fatalf("deadline in %s, want wall clock + margin", d)
	}
}

func TestARequeuedRunWaitsForItsBackoff(t *testing.T) {
	s := open(t)
	id := insertRun(t, s, "")
	if _, err := s.Claim(context.Background(), time.Hour, time.Minute); err != nil {
		t.Fatal(err)
	}
	markRunning(t, s, id, time.Hour)
	if err := s.Requeue(context.Background(), snapshot(t, s, id), time.Minute); err != nil {
		t.Fatal(err)
	}
	if claimed, _ := s.Claim(context.Background(), time.Hour, time.Minute); claimed != nil {
		t.Fatal("claimed during backoff")
	}
}

func TestTheWatchdogLosesToAFreshHeartbeat(t *testing.T) {
	s := open(t)
	id := insertRun(t, s, "")
	if _, err := s.Claim(context.Background(), time.Hour, time.Minute); err != nil {
		t.Fatal(err)
	}
	markRunning(t, s, id, time.Hour)
	observed := snapshot(t, s, id)

	// The agent heartbeats between the watchdog's read and its write.
	if _, err := s.pool.Exec(context.Background(), `UPDATE runs SET heartbeat_at = now() WHERE id = $1`, id); err != nil {
		t.Fatal(err)
	}
	if err := s.Requeue(context.Background(), observed, 0); !errors.Is(err, ErrLostRace) {
		t.Fatalf("got %v, want ErrLostRace", err)
	}
}

func TestSettlingARunDestroysItsKeys(t *testing.T) {
	s := open(t)
	id := insertRun(t, s, "")
	ctx := context.Background()
	if _, err := s.pool.Exec(ctx, `
INSERT INTO run_secrets (run_id, provider, ciphertext, expires_at)
VALUES ($1, 'tavily', 'gAAAA-ciphertext', now() + interval '1 hour')`, id); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Claim(ctx, time.Hour, time.Minute); err != nil {
		t.Fatal(err)
	}
	markRunning(t, s, id, time.Hour)
	if err := s.Fail(ctx, snapshot(t, s, id), c.CodeHeartbeatLost, "gone"); err != nil {
		t.Fatal(err)
	}
	var remaining int
	_ = s.pool.QueryRow(ctx, `SELECT count(*) FROM run_secrets WHERE run_id = $1`, id).Scan(&remaining)
	if remaining != 0 {
		t.Fatalf("%d secrets survived the terminal state", remaining)
	}
	var status, code string
	_ = s.pool.QueryRow(ctx, `SELECT status, error_code FROM runs WHERE id = $1`, id).Scan(&status, &code)
	if status != "failed" || code != c.CodeHeartbeatLost {
		t.Fatalf("status=%s code=%s", status, code)
	}
}

func TestAnUnstartedRunGetsItsAttemptBack(t *testing.T) {
	s := open(t)
	id := insertRun(t, s, "")
	claimed, err := s.Claim(context.Background(), time.Hour, time.Minute)
	if err != nil || claimed.Attempts != 1 {
		t.Fatal(err)
	}
	if err := s.ReturnUnstarted(context.Background(), id, 0); err != nil {
		t.Fatal(err)
	}
	again, _ := s.Claim(context.Background(), time.Hour, time.Minute)
	if again == nil || again.Attempts != 1 {
		t.Fatalf("again = %+v", again)
	}
}

func TestDispatcherEventsJoinTheRunTimeline(t *testing.T) {
	s := open(t)
	id := insertRun(t, s, "")
	ctx := context.Background()
	for _, message := range []string{"claimed", "dispatched"} {
		if err := s.Emit(ctx, Event{RunID: id, Level: "info", Type: "run_claimed", Message: message}); err != nil {
			t.Fatal(err)
		}
	}
	if err := s.Emit(ctx, Event{RunID: id, Level: "warn", Type: "error", Message: "x", ErrorCode: c.CodeHeartbeatLost}); err != nil {
		t.Fatal(err)
	}
	rows, err := s.pool.Query(ctx, `
SELECT seq, node, data->>'display', COALESCE(error_code, ''), expected
  FROM run_events WHERE run_id = $1 ORDER BY seq`, id)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var seqs []int
	for rows.Next() {
		var seq int
		var node, display, code string
		var expected *bool
		if err := rows.Scan(&seq, &node, &display, &code, &expected); err != nil {
			t.Fatal(err)
		}
		seqs = append(seqs, seq)
		if node != "dispatcher" {
			t.Errorf("node = %s", node)
		}
		if code != "" && (expected == nil || !*expected) {
			t.Errorf("a coded dispatcher event must be expected=true")
		}
	}
	if len(seqs) != 3 || seqs[0] != 1 || seqs[2] != 3 {
		t.Fatalf("seqs = %v", seqs)
	}
}
