// Package contracts loads the files in contracts/ that Go shares with Python: the run state
// machine and the error code taxonomy. The dispatcher refuses to start if either is missing or
// inconsistent, and every status write it makes is checked against the state machine first.
package contracts

import (
	"fmt"
	"os"
	"path/filepath"
	"slices"

	"gopkg.in/yaml.v3"
)

// Status mirrors RunStatus in agent/src/research_agent/contracts.py.
type Status string

const (
	Queued                Status = "queued"
	Dispatched            Status = "dispatched"
	Running               Status = "running"
	Succeeded             Status = "succeeded"
	SucceededWithWarnings Status = "succeeded_with_warnings"
	Failed                Status = "failed"
	Cancelled             Status = "cancelled"
)

// AllStatuses is the full vocabulary; a contract test asserts it equals the YAML.
var AllStatuses = []Status{
	Queued, Dispatched, Running, Succeeded, SucceededWithWarnings, Failed, Cancelled,
}

// Error codes the dispatcher itself produces. Each must exist in error_codes.yaml.
const (
	CodeAgentUnreachable  = "AGENT_UNREACHABLE"
	CodeHeartbeatLost     = "AGENT_HEARTBEAT_LOST"
	CodeDeadlineExceeded  = "DEADLINE_EXCEEDED"
	CodeDispatchDeferred  = "DISPATCH_DEFERRED"
	CodeUnexpectedFailure = "UNEXPECTED_EXCEPTION"
)

// CodesUsed is checked against the shared taxonomy at startup and in tests.
var CodesUsed = []string{
	CodeAgentUnreachable, CodeHeartbeatLost, CodeDeadlineExceeded, CodeDispatchDeferred,
	CodeUnexpectedFailure,
}

type stateSpec struct {
	Owner       string   `yaml:"owner"`
	Transitions []Status `yaml:"transitions"`
}

// StateMachine is contracts/run_states.yaml.
type StateMachine struct {
	Version     int                  `yaml:"version"`
	Initial     Status               `yaml:"initial"`
	Terminal    []Status             `yaml:"terminal"`
	MaxAttempts int                  `yaml:"max_attempts"`
	States      map[Status]stateSpec `yaml:"states"`
}

// CanTransition reports whether source -> target is allowed.
func (m StateMachine) CanTransition(source, target Status) bool {
	spec, ok := m.States[source]
	return ok && slices.Contains(spec.Transitions, target)
}

// IsTerminal reports whether no further transition is possible.
func (m StateMachine) IsTerminal(status Status) bool {
	return slices.Contains(m.Terminal, status)
}

// Owner is the side responsible for moving a run out of status.
func (m StateMachine) Owner(status Status) string { return m.States[status].Owner }

func (m StateMachine) validate() error {
	for _, status := range AllStatuses {
		if _, ok := m.States[status]; !ok {
			return fmt.Errorf("run_states.yaml: state %q missing", status)
		}
	}
	if len(m.States) != len(AllStatuses) {
		return fmt.Errorf("run_states.yaml: %d states, Go knows %d", len(m.States), len(AllStatuses))
	}
	for _, status := range m.Terminal {
		if len(m.States[status].Transitions) > 0 {
			return fmt.Errorf("run_states.yaml: terminal state %q has transitions", status)
		}
	}
	if m.MaxAttempts < 1 {
		return fmt.Errorf("run_states.yaml: max_attempts must be >= 1")
	}
	return nil
}

// ErrorCode is one entry of contracts/error_codes.yaml.
type ErrorCode struct {
	Category    string `yaml:"category"`
	Expected    bool   `yaml:"expected"`
	Retryable   bool   `yaml:"retryable"`
	Description string `yaml:"description"`
}

// Contracts bundles both files.
type Contracts struct {
	States StateMachine
	Codes  map[string]ErrorCode
}

// Load reads and cross-checks both contract files.
func Load(dir string) (Contracts, error) {
	var out Contracts
	if err := readYAML(filepath.Join(dir, "run_states.yaml"), &out.States); err != nil {
		return out, err
	}
	if err := out.States.validate(); err != nil {
		return out, err
	}
	var codes struct {
		Codes map[string]ErrorCode `yaml:"codes"`
	}
	if err := readYAML(filepath.Join(dir, "error_codes.yaml"), &codes); err != nil {
		return out, err
	}
	out.Codes = codes.Codes
	for _, code := range CodesUsed {
		if _, ok := out.Codes[code]; !ok {
			return out, fmt.Errorf("error_codes.yaml: the dispatcher uses %s, which is not defined", code)
		}
	}
	return out, nil
}

func readYAML(path string, target any) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read %s: %w", filepath.Base(path), err)
	}
	if err := yaml.Unmarshal(raw, target); err != nil {
		return fmt.Errorf("parse %s: %w", filepath.Base(path), err)
	}
	return nil
}
