// Package dispatcher runs the control plane's three loops: wake on NOTIFY, dispatch queued runs
// to agent replicas, and watch running ones. It holds no run state of its own - everything is in
// Postgres - so a restarted dispatcher simply carries on, and two dispatchers can run side by
// side without coordination (SKIP LOCKED, compare-and-set).
package dispatcher

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/aemreusta/apilex-test-case/dispatcher/internal/agentclient"
	"github.com/aemreusta/apilex-test-case/dispatcher/internal/capacity"
	"github.com/aemreusta/apilex-test-case/dispatcher/internal/config"
	c "github.com/aemreusta/apilex-test-case/dispatcher/internal/contracts"
	"github.com/aemreusta/apilex-test-case/dispatcher/internal/store"
	"github.com/aemreusta/apilex-test-case/dispatcher/internal/watchdog"
)

// Store is the subset of store.Store the loops use; tests supply a fake.
type Store interface {
	Claim(ctx context.Context, hardDeadline, wallClockMargin time.Duration) (*store.Claimed, error)
	ActiveCount(ctx context.Context) (int, error)
	QueuedCount(ctx context.Context) (int, error)
	Watchlist(ctx context.Context) ([]watchdog.Run, error)
	Requeue(ctx context.Context, observed watchdog.Run, backoff time.Duration) error
	ReturnUnstarted(ctx context.Context, runID, leaseID string, backoff time.Duration) error
	Fail(ctx context.Context, observed watchdog.Run, code, reason string) error
	Cancel(ctx context.Context, observed watchdog.Run, reason string) error
	RequestCancel(ctx context.Context, runID string) error
	Emit(ctx context.Context, event store.Event) error
}

// Agents is the subset of agentclient.Client the loops use.
type Agents interface {
	Health(ctx context.Context, baseURL string) (agentclient.Health, error)
	Capacity(ctx context.Context, baseURL string) (agentclient.Capacity, error)
	Execute(ctx context.Context, baseURL, runID string, req agentclient.ExecuteRequest) error
	Cancel(ctx context.Context, baseURL, runID, reason string) (bool, error)
}

// Discoverer returns the current agent base URLs.
type Discoverer func(ctx context.Context) ([]string, error)

// Dispatcher wires the loops together.
type Dispatcher struct {
	ID        string
	Config    config.Config
	Contracts c.Contracts
	Store     Store
	Agents    Agents
	Discover  Discoverer
	Log       *slog.Logger
	Now       func() time.Time

	// wake is poked by LISTEN notifications and by finished runs freeing a slot.
	wake     chan struct{}
	wakeOnce sync.Once

	mu           sync.Mutex
	lastDeferral time.Time
	agentByURL   map[string]string
}

func (d *Dispatcher) init() {
	d.wakeOnce.Do(func() {
		d.wake = make(chan struct{}, 1)
		d.agentByURL = map[string]string{}
		if d.Now == nil {
			d.Now = time.Now
		}
	})
}

// Wake asks the dispatch loop to look at the queue now instead of at its next poll.
func (d *Dispatcher) Wake() {
	d.init()
	select {
	case d.wake <- struct{}{}:
	default: // a wake-up is already pending
	}
}

// DispatchLoop claims and dispatches until ctx ends.
func (d *Dispatcher) DispatchLoop(ctx context.Context) {
	d.init()
	ticker := time.NewTicker(d.Config.PollInterval())
	defer ticker.Stop()
	for {
		// Drain the queue as far as capacity allows, then sleep until woken or polled.
		for {
			dispatched, err := d.DispatchOnce(ctx)
			if err != nil && ctx.Err() == nil {
				d.Log.Error("dispatch failed", "error", err)
			}
			if !dispatched || ctx.Err() != nil {
				break
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-d.wake:
		case <-ticker.C:
		}
	}
}

// WatchLoop runs the watchdog every tick until ctx ends.
func (d *Dispatcher) WatchLoop(ctx context.Context) {
	d.init()
	ticker := time.NewTicker(d.Config.WatchdogTick())
	defer ticker.Stop()
	for {
		if err := d.WatchOnce(ctx); err != nil && ctx.Err() == nil {
			d.Log.Error("watchdog pass failed", "error", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

// DispatchOnce hands at most one run to an agent. It reports whether it did, so the caller
// knows whether to try again immediately.
func (d *Dispatcher) DispatchOnce(ctx context.Context) (bool, error) {
	d.init()
	active, err := d.Store.ActiveCount(ctx)
	if err != nil {
		return false, err
	}
	if !capacity.HasRoom(active, d.Config.Capacity.MaxConcurrentRuns) {
		d.deferred(ctx, fmt.Sprintf("global limit reached (%d active)", active))
		return false, nil
	}

	replicas := d.survey(ctx)
	ranked := capacity.Rank(replicas)
	if len(ranked) == 0 {
		d.deferred(ctx, fmt.Sprintf("no agent replica with a free slot (%d known)", len(replicas)))
		return false, nil
	}

	claimed, err := d.Store.Claim(ctx, d.Config.HardDeadline(), d.Config.WallClockMargin())
	if err != nil || claimed == nil {
		return false, err
	}
	log := d.Log.With("run_id", claimed.ID, "attempt", claimed.Attempts)
	d.emit(ctx, store.Event{
		RunID: claimed.ID, Level: "info", Type: "run_claimed",
		Message: fmt.Sprintf("Claimed run (attempt %d of %d); hard deadline %s.",
			claimed.Attempts, d.Contracts.States.MaxAttempts, claimed.DeadlineAt.UTC().Format(time.RFC3339)),
		Data: map[string]any{"attempt": claimed.Attempts, "dispatcher_id": d.ID,
			"deadline_at": claimed.DeadlineAt.UTC().Format(time.RFC3339)},
	})

	request := agentclient.ExecuteRequest{
		Attempt: claimed.Attempts, LeaseID: claimed.LeaseID, DeadlineAt: &claimed.DeadlineAt, DispatcherID: d.ID,
	}
	var failures []string
	for _, replica := range ranked {
		err := d.Agents.Execute(ctx, replica.URL, claimed.ID, request)
		if err == nil {
			log.Info("run dispatched", "agent_url", replica.URL, "agent_id", replica.AgentID)
			d.emit(ctx, store.Event{
				RunID: claimed.ID, Level: "info", Type: "run_dispatched",
				Message: fmt.Sprintf("Dispatched to %s (%d free slots).", replica.AgentID, replica.SlotsFree),
				Data:    map[string]any{"agent_id": replica.AgentID, "slots_free": replica.SlotsFree},
			})
			return true, nil
		}
		if errors.Is(err, agentclient.ErrAlreadyRunning) {
			// The agent owns it already (a retried call whose first response was lost).
			return true, nil
		}
		failures = append(failures, fmt.Sprintf("%s: %v", replica.AgentID, err))
		log.Warn("replica refused run", "agent_url", replica.URL, "error", err)
	}

	backoff := d.Config.Backoff(2)
	if err := d.Store.ReturnUnstarted(ctx, claimed.ID, claimed.LeaseID, backoff); err != nil && !errors.Is(err, store.ErrLostRace) {
		return false, fmt.Errorf("return run %s to queue: %w", claimed.ID, err)
	}
	d.emit(ctx, store.Event{
		RunID: claimed.ID, Level: "warn", Type: "error", ErrorCode: c.CodeAgentUnreachable,
		Message: fmt.Sprintf("%s: no replica accepted the run -> back to queue -> retry in %s",
			c.CodeAgentUnreachable, backoff),
		Data: map[string]any{"decision": "return to queue", "outcome": fmt.Sprintf("retry in %s", backoff),
			"failures": failures},
	})
	return false, nil
}

// survey asks every discovered replica for health and capacity, in parallel.
func (d *Dispatcher) survey(ctx context.Context) []capacity.Replica {
	d.init()
	urls, err := d.Discover(ctx)
	if err != nil {
		d.Log.Warn("agent discovery failed", "error", err)
		return nil
	}
	replicas := make([]capacity.Replica, len(urls))
	var wg sync.WaitGroup
	for i, url := range urls {
		wg.Add(1)
		go func() {
			defer wg.Done()
			replica := capacity.Replica{URL: url}
			if health, err := d.Agents.Health(ctx, url); err == nil && health.Status == "ok" {
				replica.AgentID = health.AgentID
				if slots, err := d.Agents.Capacity(ctx, url); err == nil {
					replica.Healthy = true
					replica.SlotsFree = slots.SlotsFree
				}
			}
			replicas[i] = replica
		}()
	}
	wg.Wait()

	d.mu.Lock()
	for _, replica := range replicas {
		if replica.AgentID != "" {
			d.agentByURL[replica.AgentID] = replica.URL
		}
	}
	d.mu.Unlock()
	return replicas
}

// deferred logs saturation at most once a minute, and only when something is actually waiting.
func (d *Dispatcher) deferred(ctx context.Context, reason string) {
	queued, err := d.Store.QueuedCount(ctx)
	if err != nil || queued == 0 {
		return
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.Now().Sub(d.lastDeferral) < time.Minute {
		return
	}
	d.lastDeferral = d.Now()
	d.Log.Info("dispatch deferred", "error_code", c.CodeDispatchDeferred, "reason", reason, "queued", queued)
}

// WatchOnce applies the watchdog decision to every run on the watchlist.
func (d *Dispatcher) WatchOnce(ctx context.Context) error {
	d.init()
	runs, err := d.Store.Watchlist(ctx)
	if err != nil {
		return err
	}
	limits := watchdog.Limits{
		HeartbeatTimeout: d.Config.HeartbeatTimeout(),
		DispatchTimeout:  d.Config.DispatchTimeout(),
		CancelGrace:      d.Config.CancelGrace(),
		MaxAttempts:      d.Contracts.States.MaxAttempts,
	}
	now := d.Now()
	for _, run := range runs {
		decision := watchdog.Decide(run, now, limits)
		if decision.Kind == watchdog.Nothing {
			continue
		}
		if err := d.apply(ctx, run, decision); err != nil {
			if errors.Is(err, store.ErrLostRace) {
				// The agent got there first (it finished or heartbeated). Nothing to do.
				continue
			}
			d.Log.Error("watchdog action failed", "run_id", run.ID, "action", decision.Kind.String(), "error", err)
		}
	}
	return nil
}

func (d *Dispatcher) apply(ctx context.Context, run watchdog.Run, decision watchdog.Decision) error {
	log := d.Log.With("run_id", run.ID, "error_code", decision.Code, "action", decision.Kind.String())
	var err error
	var eventType, outcome string
	level := "warn"

	switch decision.Kind {
	case watchdog.Requeue:
		backoff := d.Config.Backoff(run.Attempts + 1)
		err = d.Store.Requeue(ctx, run, backoff)
		eventType, outcome = "heartbeat_lost", fmt.Sprintf("requeued, retry in %s from the last checkpoint", backoff)
		if decision.Code == c.CodeAgentUnreachable {
			eventType = "error"
		}
		d.Wake()
	case watchdog.Fail:
		err = d.Store.Fail(ctx, run, decision.Code, decision.Reason)
		eventType, outcome, level = "run_finished", "run failed", "error"
		if decision.Code == c.CodeDeadlineExceeded {
			eventType = "deadline_exceeded"
		}
	case watchdog.Cancel:
		err = d.Store.Cancel(ctx, run, decision.Reason)
		eventType, outcome = "run_cancelled", "run cancelled"
	case watchdog.AskAgentToCancel:
		if err = d.Store.RequestCancel(ctx, run.ID); err == nil {
			d.forwardCancel(ctx, run, "deadline_exceeded")
		}
		eventType = "deadline_exceeded"
		outcome = fmt.Sprintf("agent asked to stop; failed in %s if it does not", d.Config.CancelGrace())
	}
	if err != nil {
		return err
	}

	log.Warn(decision.Reason)
	if decision.Kind == watchdog.Fail || decision.Kind == watchdog.Cancel {
		return nil // Store committed the terminal event with the status.
	}
	message := decision.Reason + " -> " + outcome
	if decision.Code != "" {
		message = decision.Code + ": " + message
	}
	d.emit(ctx, store.Event{
		RunID: run.ID, Level: level, Type: eventType, ErrorCode: decision.Code, Message: message,
		Data: map[string]any{"decision": decision.Kind.String(), "outcome": outcome,
			"attempt": run.Attempts, "agent_id": run.AgentID},
	})
	return nil
}

// forwardCancel is best effort: the database flag already guarantees the agent will stop.
func (d *Dispatcher) forwardCancel(ctx context.Context, run watchdog.Run, reason string) {
	d.mu.Lock()
	url, ok := d.agentByURL[run.AgentID]
	d.mu.Unlock()
	if !ok {
		return
	}
	if _, err := d.Agents.Cancel(ctx, url, run.ID, reason); err != nil {
		d.Log.Warn("cancel forward failed; relying on the database flag", "run_id", run.ID, "error", err)
	}
}

func (d *Dispatcher) emit(ctx context.Context, event store.Event) {
	if err := d.Store.Emit(ctx, event); err != nil {
		d.Log.Warn("could not write run event", "run_id", event.RunID, "error", err)
	}
}
