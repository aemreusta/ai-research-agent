package contracts

import (
	"path/filepath"
	"runtime"
	"testing"
)

func load(t *testing.T) Contracts {
	t.Helper()
	_, file, _, _ := runtime.Caller(0)
	c, err := Load(filepath.Join(filepath.Dir(file), "..", "..", "..", "contracts"))
	if err != nil {
		t.Fatalf("load contracts: %v", err)
	}
	return c
}

func TestGoKnowsExactlyTheContractStates(t *testing.T) {
	c := load(t)
	if len(c.States.States) != len(AllStatuses) {
		t.Fatalf("contract has %d states, Go has %d", len(c.States.States), len(AllStatuses))
	}
}

// The transitions the dispatcher performs, each of which must be legal. If the contract changes
// under one of these, the dispatcher would start writing illegal states - this catches it.
func TestEveryTransitionTheDispatcherMakesIsAllowed(t *testing.T) {
	c := load(t)
	moves := []struct{ from, to Status }{
		{Queued, Dispatched},    // claim
		{Dispatched, Queued},    // no replica accepted it / dispatch timeout
		{Dispatched, Failed},    // attempts exhausted, deadline
		{Running, Queued},       // heartbeat lost
		{Running, Failed},       // heartbeat lost with attempts exhausted, deadline
		{Running, Cancelled},    // cancel requested and agent gone
		{Dispatched, Cancelled}, // cancel before the agent took it
		{Queued, Failed},        // deadline while queued
	}
	for _, move := range moves {
		if !c.States.CanTransition(move.from, move.to) {
			t.Errorf("%s -> %s is not allowed by run_states.yaml", move.from, move.to)
		}
	}
}

func TestTerminalStatesAreFinal(t *testing.T) {
	c := load(t)
	for _, status := range c.States.Terminal {
		for _, target := range AllStatuses {
			if c.States.CanTransition(status, target) {
				t.Errorf("terminal %s -> %s is allowed", status, target)
			}
		}
	}
}

func TestEveryCodeTheDispatcherEmitsIsInTheTaxonomy(t *testing.T) {
	c := load(t)
	for _, code := range CodesUsed {
		spec, ok := c.Codes[code]
		if !ok {
			t.Errorf("%s is missing from error_codes.yaml", code)
			continue
		}
		if code != CodeUnexpectedFailure && !spec.Expected {
			t.Errorf("%s should be an expected (handled) condition", code)
		}
	}
}

func TestMaxAttemptsIsPositive(t *testing.T) {
	c := load(t)
	if c.States.MaxAttempts < 1 {
		t.Errorf("max_attempts = %d", c.States.MaxAttempts)
	}
}
