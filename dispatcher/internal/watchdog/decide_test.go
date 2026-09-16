package watchdog

import (
	"testing"
	"time"

	c "github.com/aemreusta/apilex-test-case/dispatcher/internal/contracts"
)

var (
	now    = time.Date(2026, 9, 16, 12, 0, 0, 0, time.UTC)
	limits = Limits{
		HeartbeatTimeout: 30 * time.Second,
		DispatchTimeout:  30 * time.Second,
		CancelGrace:      15 * time.Second,
		MaxAttempts:      3,
	}
)

func ago(d time.Duration) *time.Time { t := now.Add(-d); return &t }
func in(d time.Duration) *time.Time  { t := now.Add(d); return &t }

func TestDecide(t *testing.T) {
	cases := []struct {
		name string
		run  Run
		want Kind
		code string
	}{
		// queued
		{"queued and on time", Run{Status: c.Queued}, Nothing, ""},
		{"queued past its deadline", Run{Status: c.Queued, DeadlineAt: ago(time.Second)}, Fail, c.CodeDeadlineExceeded},

		// dispatched
		{"just dispatched", Run{Status: c.Dispatched, Attempts: 1, DispatchedAt: ago(5 * time.Second)}, Nothing, ""},
		{"dispatched but never started", Run{Status: c.Dispatched, Attempts: 1, DispatchedAt: ago(time.Minute)}, Requeue, c.CodeAgentUnreachable},
		{"never started, attempts spent", Run{Status: c.Dispatched, Attempts: 3, DispatchedAt: ago(time.Minute)}, Fail, c.CodeAgentUnreachable},
		{"never started, cancel pending", Run{Status: c.Dispatched, Attempts: 1, CancelRequested: true, DispatchedAt: ago(time.Minute)}, Cancel, ""},
		{"dispatched past deadline", Run{Status: c.Dispatched, Attempts: 1, DispatchedAt: ago(time.Second), DeadlineAt: ago(time.Second)}, Fail, c.CodeDeadlineExceeded},

		// running
		{"healthy", Run{Status: c.Running, Attempts: 1, HeartbeatAt: ago(5 * time.Second), DeadlineAt: in(time.Hour)}, Nothing, ""},
		{"heartbeat exactly at the limit is still healthy", Run{Status: c.Running, Attempts: 1, HeartbeatAt: ago(30 * time.Second)}, Nothing, ""},
		{"heartbeat lost, first attempt", Run{Status: c.Running, Attempts: 1, HeartbeatAt: ago(31 * time.Second)}, Requeue, c.CodeHeartbeatLost},
		{"heartbeat lost, second attempt", Run{Status: c.Running, Attempts: 2, HeartbeatAt: ago(time.Minute)}, Requeue, c.CodeHeartbeatLost},
		{"heartbeat lost, attempts spent", Run{Status: c.Running, Attempts: 3, HeartbeatAt: ago(time.Minute)}, Fail, c.CodeHeartbeatLost},
		{"no heartbeat ever", Run{Status: c.Running, Attempts: 1}, Requeue, c.CodeHeartbeatLost},
		{"heartbeat lost after a cancel request", Run{Status: c.Running, Attempts: 1, CancelRequested: true, HeartbeatAt: ago(time.Minute)}, Cancel, c.CodeHeartbeatLost},
		{"deadline reached: ask first", Run{Status: c.Running, Attempts: 1, HeartbeatAt: ago(time.Second), DeadlineAt: ago(time.Second)}, AskAgentToCancel, c.CodeDeadlineExceeded},
		{"deadline reached, already asked, within grace", Run{Status: c.Running, Attempts: 1, CancelRequested: true, HeartbeatAt: ago(time.Second), DeadlineAt: ago(10 * time.Second)}, Nothing, ""},
		{"deadline plus grace passed", Run{Status: c.Running, Attempts: 1, CancelRequested: true, HeartbeatAt: ago(time.Second), DeadlineAt: ago(16 * time.Second)}, Fail, c.CodeDeadlineExceeded},
		{"deadline beats a lost heartbeat: no fresh attempt past the deadline", Run{Status: c.Running, Attempts: 1, HeartbeatAt: ago(time.Hour), DeadlineAt: ago(time.Hour)}, Fail, c.CodeDeadlineExceeded},

		// terminal
		{"succeeded runs are left alone", Run{Status: c.Succeeded, DeadlineAt: ago(time.Hour)}, Nothing, ""},
		{"failed runs are left alone", Run{Status: c.Failed, HeartbeatAt: ago(time.Hour)}, Nothing, ""},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := Decide(tc.run, now, limits)
			if got.Kind != tc.want {
				t.Fatalf("kind = %s, want %s (reason: %s)", got.Kind, tc.want, got.Reason)
			}
			if got.Code != tc.code {
				t.Errorf("code = %q, want %q", got.Code, tc.code)
			}
			if got.Kind != Nothing && got.Reason == "" {
				t.Error("every action needs a reason for the run timeline")
			}
		})
	}
}

// The decisions must only ever produce legal transitions for the status they are applied to.
func TestDecisionsMapToLegalTransitions(t *testing.T) {
	targets := map[Kind]c.Status{Requeue: c.Queued, Fail: c.Failed, Cancel: c.Cancelled}
	machine := loadMachine(t)
	for _, status := range []c.Status{c.Queued, c.Dispatched, c.Running} {
		for kind, target := range targets {
			if kind == Requeue && status == c.Queued {
				continue
			}
			if kind == Cancel && status == c.Queued {
				continue // the api settles queued cancels itself
			}
			if !machine.CanTransition(status, target) {
				t.Errorf("%s from %s -> %s is illegal", kind, status, target)
			}
		}
	}
}
