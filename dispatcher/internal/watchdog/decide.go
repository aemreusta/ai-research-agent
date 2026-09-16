// Package watchdog decides what to do about runs whose agent has gone quiet or whose time is up.
//
// The decision is a pure function of a run snapshot and the clock - no database, no HTTP - so
// every rule is covered by a table-driven test and the side effects live elsewhere. This is the
// dispatcher's reason to exist: whatever the agent does, a run cannot outlive its deadline and
// a crashed agent's run is not lost (architecture v0.6 §2, Design Question 2).
package watchdog

import (
	"fmt"
	"time"

	"github.com/aemreusta/apilex-test-case/dispatcher/internal/contracts"
)

// Run is the slice of a runs row the watchdog looks at.
type Run struct {
	ID              string
	Status          contracts.Status
	Attempts        int
	CancelRequested bool
	DispatchedAt    *time.Time
	HeartbeatAt     *time.Time
	DeadlineAt      *time.Time
	AgentID         string
}

// Kind is what the watchdog wants done.
type Kind int

const (
	// Nothing: the run is healthy.
	Nothing Kind = iota
	// Requeue: send it back to the queue for another agent (resumes from the checkpoint).
	Requeue
	// Fail: terminal failure with Code.
	Fail
	// Cancel: terminal cancellation (a cancel was requested and nobody is left to honour it).
	Cancel
	// AskAgentToCancel: the deadline passed; the agent gets a grace period to stop cleanly.
	AskAgentToCancel
)

func (k Kind) String() string {
	return [...]string{"nothing", "requeue", "fail", "cancel", "ask_agent_to_cancel"}[k]
}

// Decision is the outcome plus the reason that goes onto the run timeline.
type Decision struct {
	Kind   Kind
	Code   string
	Reason string
}

// Limits are the watchdog's thresholds (config/dispatcher.yaml).
type Limits struct {
	HeartbeatTimeout time.Duration
	DispatchTimeout  time.Duration
	CancelGrace      time.Duration
	MaxAttempts      int
}

// Decide applies the rules in priority order. Deadline beats everything: it is the outer
// guarantee, and a run past it must not be requeued into another full attempt.
func Decide(run Run, now time.Time, limits Limits) Decision {
	switch run.Status {
	case contracts.Queued:
		if pastDeadline(run, now, 0) {
			return Decision{Fail, contracts.CodeDeadlineExceeded, "deadline passed while queued"}
		}
		return Decision{Kind: Nothing}

	case contracts.Dispatched:
		if pastDeadline(run, now, 0) {
			return Decision{Fail, contracts.CodeDeadlineExceeded, "deadline passed before an agent started the run"}
		}
		if olderThan(run.DispatchedAt, now, limits.DispatchTimeout) {
			if run.CancelRequested {
				return Decision{Cancel, "", "cancel requested and no agent took the run"}
			}
			return retryOrFail(run, limits, contracts.CodeAgentUnreachable,
				fmt.Sprintf("no agent started the run within %s", limits.DispatchTimeout))
		}
		return Decision{Kind: Nothing}

	case contracts.Running:
		if pastDeadline(run, now, limits.CancelGrace) {
			return Decision{Fail, contracts.CodeDeadlineExceeded,
				fmt.Sprintf("agent did not stop within %s of the deadline", limits.CancelGrace)}
		}
		if pastDeadline(run, now, 0) && !run.CancelRequested {
			return Decision{AskAgentToCancel, contracts.CodeDeadlineExceeded, "deadline reached; asking the agent to stop"}
		}
		if olderThan(run.HeartbeatAt, now, limits.HeartbeatTimeout) {
			if run.CancelRequested {
				// Resuming a run the user asked to stop would be perverse.
				return Decision{Cancel, contracts.CodeHeartbeatLost, "agent went silent after a cancel request"}
			}
			return retryOrFail(run, limits, contracts.CodeHeartbeatLost,
				fmt.Sprintf("no heartbeat for more than %s", limits.HeartbeatTimeout))
		}
		return Decision{Kind: Nothing}
	}
	return Decision{Kind: Nothing}
}

func retryOrFail(run Run, limits Limits, code, reason string) Decision {
	if run.Attempts >= limits.MaxAttempts {
		return Decision{Fail, code, fmt.Sprintf("%s; giving up after %d attempts", reason, run.Attempts)}
	}
	return Decision{Requeue, code, fmt.Sprintf("%s; requeueing (attempt %d of %d used)", reason, run.Attempts, limits.MaxAttempts)}
}

func pastDeadline(run Run, now time.Time, grace time.Duration) bool {
	return run.DeadlineAt != nil && now.After(run.DeadlineAt.Add(grace))
}

// olderThan treats a missing timestamp as stale: a running run with no heartbeat at all is
// exactly the case the watchdog exists for.
func olderThan(stamp *time.Time, now time.Time, limit time.Duration) bool {
	return stamp == nil || now.Sub(*stamp) > limit
}
