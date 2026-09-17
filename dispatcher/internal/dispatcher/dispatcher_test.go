package dispatcher

import (
	"context"
	"io"
	"log/slog"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aemreusta/apilex-test-case/dispatcher/internal/agentclient"
	"github.com/aemreusta/apilex-test-case/dispatcher/internal/config"
	c "github.com/aemreusta/apilex-test-case/dispatcher/internal/contracts"
	"github.com/aemreusta/apilex-test-case/dispatcher/internal/store"
	"github.com/aemreusta/apilex-test-case/dispatcher/internal/watchdog"
)

// --- fakes -------------------------------------------------------------------

type fakeStore struct {
	mu        sync.Mutex
	active    int
	queued    int
	claimable []string
	watch     []watchdog.Run
	returned  []string
	requeued  []string
	failed    map[string]string
	cancelled []string
	flagged   []string
	events    []store.Event
	lostRace  bool
}

func (s *fakeStore) Claim(context.Context, time.Duration, time.Duration) (*store.Claimed, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.claimable) == 0 {
		return nil, nil
	}
	id := s.claimable[0]
	s.claimable = s.claimable[1:]
	s.active++
	return &store.Claimed{ID: id, Attempts: 1, DeadlineAt: time.Now().Add(time.Hour)}, nil
}
func (s *fakeStore) ActiveCount(context.Context) (int, error) { return s.active, nil }
func (s *fakeStore) QueuedCount(context.Context) (int, error) { return s.queued, nil }
func (s *fakeStore) Watchlist(context.Context) ([]watchdog.Run, error) {
	return s.watch, nil
}
func (s *fakeStore) Requeue(_ context.Context, run watchdog.Run, _ time.Duration) error {
	if s.lostRace {
		return store.ErrLostRace
	}
	s.requeued = append(s.requeued, run.ID)
	return nil
}
func (s *fakeStore) ReturnUnstarted(_ context.Context, id, _ string, _ time.Duration) error {
	s.returned = append(s.returned, id)
	s.active--
	return nil
}
func (s *fakeStore) Fail(_ context.Context, run watchdog.Run, code, _ string) error {
	if s.failed == nil {
		s.failed = map[string]string{}
	}
	s.failed[run.ID] = code
	return nil
}
func (s *fakeStore) Cancel(_ context.Context, run watchdog.Run, _ string) error {
	s.cancelled = append(s.cancelled, run.ID)
	return nil
}
func (s *fakeStore) RequestCancel(_ context.Context, id string) error {
	s.flagged = append(s.flagged, id)
	return nil
}
func (s *fakeStore) Emit(_ context.Context, event store.Event) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.events = append(s.events, event)
	return nil
}

type fakeAgent struct {
	healthy    bool
	free       int
	executeErr error
	executed   []string
	cancelled  []string
}

type fakeAgents map[string]*fakeAgent

func (f fakeAgents) Health(_ context.Context, url string) (agentclient.Health, error) {
	agent := f[url]
	if !agent.healthy {
		return agentclient.Health{}, agentclient.ErrUnreachable
	}
	return agentclient.Health{Status: "ok", AgentID: "id-" + url}, nil
}
func (f fakeAgents) Capacity(_ context.Context, url string) (agentclient.Capacity, error) {
	return agentclient.Capacity{AgentID: "id-" + url, SlotsFree: f[url].free}, nil
}
func (f fakeAgents) Execute(_ context.Context, url, id string, _ agentclient.ExecuteRequest) error {
	agent := f[url]
	if agent.executeErr != nil {
		return agent.executeErr
	}
	agent.executed = append(agent.executed, id)
	return nil
}
func (f fakeAgents) Cancel(_ context.Context, url, id, _ string) (bool, error) {
	f[url].cancelled = append(f[url].cancelled, id)
	return true, nil
}

func newDispatcher(t *testing.T, s *fakeStore, agents fakeAgents) *Dispatcher {
	t.Helper()
	_, file, _, _ := runtime.Caller(0)
	root := filepath.Join(filepath.Dir(file), "..", "..", "..")
	cfg, err := config.Load(filepath.Join(root, "config"))
	if err != nil {
		t.Fatal(err)
	}
	contracts, err := c.Load(filepath.Join(root, "contracts"))
	if err != nil {
		t.Fatal(err)
	}
	urls := make([]string, 0, len(agents))
	for url := range agents {
		urls = append(urls, url)
	}
	return &Dispatcher{
		ID: "d-test", Config: cfg, Contracts: contracts, Store: s, Agents: agents,
		Discover: func(context.Context) ([]string, error) { return urls, nil },
		Log:      slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
}

// --- dispatch ----------------------------------------------------------------

func TestARunGoesToTheEmptiestHealthyReplica(t *testing.T) {
	s := &fakeStore{claimable: []string{"run-1"}, queued: 1}
	agents := fakeAgents{
		"a": {healthy: true, free: 1},
		"b": {healthy: true, free: 2},
		"c": {healthy: false, free: 9},
	}
	d := newDispatcher(t, s, agents)

	dispatched, err := d.DispatchOnce(context.Background())
	if err != nil || !dispatched {
		t.Fatalf("dispatched=%v err=%v", dispatched, err)
	}
	if len(agents["b"].executed) != 1 || len(agents["a"].executed) != 0 {
		t.Fatalf("a=%v b=%v", agents["a"].executed, agents["b"].executed)
	}
	types := []string{s.events[0].Type, s.events[1].Type}
	if strings.Join(types, ",") != "run_claimed,run_dispatched" {
		t.Errorf("timeline = %v", types)
	}
}

func TestNothingIsClaimedWithoutAFreeSlot(t *testing.T) {
	s := &fakeStore{claimable: []string{"run-1"}, queued: 1}
	d := newDispatcher(t, s, fakeAgents{"a": {healthy: true, free: 0}})

	dispatched, _ := d.DispatchOnce(context.Background())
	if dispatched || len(s.claimable) != 1 {
		t.Fatal("a run was claimed although no replica could take it")
	}
}

func TestTheGlobalLimitHoldsBackClaims(t *testing.T) {
	s := &fakeStore{claimable: []string{"run-1"}, active: 4, queued: 1}
	d := newDispatcher(t, s, fakeAgents{"a": {healthy: true, free: 2}})

	if dispatched, _ := d.DispatchOnce(context.Background()); dispatched || len(s.claimable) != 1 {
		t.Fatal("claimed past max_concurrent_runs")
	}
}

func TestAFailingReplicaFallsBackToTheNextOne(t *testing.T) {
	s := &fakeStore{claimable: []string{"run-1"}, queued: 1}
	agents := fakeAgents{
		"a": {healthy: true, free: 5, executeErr: agentclient.ErrUnreachable},
		"b": {healthy: true, free: 1},
	}
	d := newDispatcher(t, s, agents)

	if dispatched, _ := d.DispatchOnce(context.Background()); !dispatched {
		t.Fatal("expected fallback to b")
	}
	if len(agents["b"].executed) != 1 {
		t.Fatal("b did not get the run")
	}
}

func TestWhenEveryReplicaRefusesTheRunGoesBackToTheQueue(t *testing.T) {
	s := &fakeStore{claimable: []string{"run-1"}, queued: 1}
	agents := fakeAgents{
		"a": {healthy: true, free: 1, executeErr: agentclient.ErrNoCapacity},
		"b": {healthy: true, free: 1, executeErr: agentclient.ErrUnreachable},
	}
	d := newDispatcher(t, s, agents)

	dispatched, err := d.DispatchOnce(context.Background())
	if err != nil || dispatched {
		t.Fatalf("dispatched=%v err=%v", dispatched, err)
	}
	if len(s.returned) != 1 || s.returned[0] != "run-1" {
		t.Fatalf("returned = %v", s.returned)
	}
	last := s.events[len(s.events)-1]
	if last.ErrorCode != c.CodeAgentUnreachable || !strings.Contains(last.Message, "back to queue") {
		t.Errorf("event = %+v", last)
	}
}

func TestAnAgentThatAlreadyOwnsTheRunCountsAsDispatched(t *testing.T) {
	s := &fakeStore{claimable: []string{"run-1"}, queued: 1}
	d := newDispatcher(t, s, fakeAgents{"a": {healthy: true, free: 1, executeErr: agentclient.ErrAlreadyRunning}})
	if dispatched, _ := d.DispatchOnce(context.Background()); !dispatched || len(s.returned) != 0 {
		t.Fatal("a 409 must not send the run back to the queue")
	}
}

// --- watchdog ----------------------------------------------------------------

func stale() *time.Time { t := time.Now().Add(-time.Hour); return &t }

func TestTheWatchdogRequeuesARunWhoseAgentDied(t *testing.T) {
	s := &fakeStore{watch: []watchdog.Run{{ID: "r", Status: c.Running, Attempts: 1, HeartbeatAt: stale()}}}
	d := newDispatcher(t, s, fakeAgents{})
	if err := d.WatchOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(s.requeued) != 1 {
		t.Fatalf("requeued = %v", s.requeued)
	}
	event := s.events[0]
	if event.ErrorCode != c.CodeHeartbeatLost || event.Data["decision"] != "requeue" {
		t.Errorf("event = %+v", event)
	}
}

func TestTheWatchdogFailsARunPastItsDeadlineAndGrace(t *testing.T) {
	deadline := time.Now().Add(-time.Minute)
	s := &fakeStore{watch: []watchdog.Run{{
		ID: "r", Status: c.Running, Attempts: 1, CancelRequested: true,
		HeartbeatAt: ptr(time.Now()), DeadlineAt: &deadline,
	}}}
	d := newDispatcher(t, s, fakeAgents{})
	_ = d.WatchOnce(context.Background())
	if s.failed["r"] != c.CodeDeadlineExceeded {
		t.Fatalf("failed = %v", s.failed)
	}
}

func TestAtTheDeadlineTheAgentIsAskedFirst(t *testing.T) {
	deadline := time.Now().Add(-time.Second)
	s := &fakeStore{watch: []watchdog.Run{{
		ID: "r", Status: c.Running, Attempts: 1, AgentID: "id-a",
		HeartbeatAt: ptr(time.Now()), DeadlineAt: &deadline,
	}}}
	agents := fakeAgents{"a": {healthy: true, free: 1}}
	d := newDispatcher(t, s, agents)
	d.survey(context.Background()) // learn which URL belongs to id-a

	_ = d.WatchOnce(context.Background())
	if len(s.flagged) != 1 {
		t.Fatal("the cancel flag was not set")
	}
	if len(agents["a"].cancelled) != 1 {
		t.Fatal("the cancel was not forwarded to the owning agent")
	}
	if len(s.failed) != 0 {
		t.Fatal("failed before the grace period")
	}
}

func TestLosingARaceToTheAgentIsNotAnError(t *testing.T) {
	s := &fakeStore{
		lostRace: true,
		watch:    []watchdog.Run{{ID: "r", Status: c.Running, Attempts: 1, HeartbeatAt: stale()}},
	}
	d := newDispatcher(t, s, fakeAgents{})
	if err := d.WatchOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	if len(s.events) != 0 {
		t.Fatal("no event should be written for an action that did not happen")
	}
}

func ptr[T any](v T) *T { return &v }
