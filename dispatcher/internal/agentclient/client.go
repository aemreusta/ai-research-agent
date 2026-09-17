// Package agentclient calls the Python agent replicas, per contracts/agent-api.openapi.yaml.
//
// The client never carries secrets or run content: execute sends a run id and an attempt
// number, and the agent reads everything else from Postgres itself.
package agentclient

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"
)

// Paths from the OpenAPI contract; a test asserts each one is declared there.
const (
	PathHealthz  = "/healthz"
	PathCapacity = "/capacity"
	PathExecute  = "/v1/runs/%s/execute"
	PathCancel   = "/v1/runs/%s/cancel"
)

// Sentinel errors, so the dispatcher branches on meaning rather than status codes.
var (
	ErrNoCapacity     = errors.New("agent has no free slot")
	ErrAlreadyRunning = errors.New("agent is already executing this run")
	ErrRunNotFound    = errors.New("agent does not know this run")
	ErrUnreachable    = errors.New("agent unreachable")
)

// Health is the /healthz body.
type Health struct {
	Status  string `json:"status"`
	AgentID string `json:"agent_id"`
	Version string `json:"version"`
}

// Capacity is the /capacity body.
type Capacity struct {
	AgentID       string   `json:"agent_id"`
	SlotsTotal    int      `json:"slots_total"`
	SlotsUsed     int      `json:"slots_used"`
	SlotsFree     int      `json:"slots_free"`
	RunningRunIDs []string `json:"running_run_ids"`
}

// ExecuteRequest is the execute body. Unknown fields are rejected by the agent, so this struct
// is the whole vocabulary.
type ExecuteRequest struct {
	LeaseID      string     `json:"lease_id"`
	Attempt      int        `json:"attempt"`
	DeadlineAt   *time.Time `json:"deadline_at,omitempty"`
	DispatcherID string     `json:"dispatcher_id,omitempty"`
}

type cancelRequest struct {
	Reason string `json:"reason,omitempty"`
}

type cancelAccepted struct {
	Accepted   bool   `json:"accepted"`
	RunID      string `json:"run_id"`
	WasRunning bool   `json:"was_running"`
}

// ErrorBody is the contract's Error schema.
type ErrorBody struct {
	ErrorCode string `json:"error_code"`
	Message   string `json:"message"`
	Expected  bool   `json:"expected"`
}

// Client is safe for concurrent use.
type Client struct {
	http           *http.Client
	healthTimeout  time.Duration
	executeTimeout time.Duration
}

// New builds a client with per-call timeouts from config/dispatcher.yaml.
func New(healthTimeout, executeTimeout time.Duration) *Client {
	return &Client{
		http:           &http.Client{Transport: http.DefaultTransport},
		healthTimeout:  healthTimeout,
		executeTimeout: executeTimeout,
	}
}

// Health asks whether the replica accepts work. A draining replica answers 503 with a body.
func (c *Client) Health(ctx context.Context, baseURL string) (Health, error) {
	var out Health
	status, err := c.do(ctx, c.healthTimeout, http.MethodGet, baseURL+PathHealthz, nil, &out)
	if err != nil {
		return out, err
	}
	if status != http.StatusOK {
		return out, fmt.Errorf("%w: health status %d (%s)", ErrUnreachable, status, out.Status)
	}
	return out, nil
}

// Capacity returns the replica's slot usage.
func (c *Client) Capacity(ctx context.Context, baseURL string) (Capacity, error) {
	var out Capacity
	status, err := c.do(ctx, c.healthTimeout, http.MethodGet, baseURL+PathCapacity, nil, &out)
	if err != nil {
		return out, err
	}
	if status != http.StatusOK {
		return out, fmt.Errorf("%w: capacity status %d", ErrUnreachable, status)
	}
	return out, nil
}

// Execute hands a claimed run to the replica. nil means 202: the run is now the agent's.
func (c *Client) Execute(ctx context.Context, baseURL, runID string, req ExecuteRequest) error {
	var body ErrorBody
	status, err := c.do(ctx, c.executeTimeout, http.MethodPost,
		baseURL+fmt.Sprintf(PathExecute, runID), req, &body)
	if err != nil {
		return err
	}
	switch status {
	case http.StatusAccepted:
		return nil
	case http.StatusServiceUnavailable:
		return fmt.Errorf("%w: %s", ErrNoCapacity, body.Message)
	case http.StatusConflict:
		return fmt.Errorf("%w: %s", ErrAlreadyRunning, body.Message)
	case http.StatusNotFound:
		return fmt.Errorf("%w: %s", ErrRunNotFound, body.Message)
	default:
		return fmt.Errorf("%w: execute status %d (%s %s)", ErrUnreachable, status, body.ErrorCode, body.Message)
	}
}

// Cancel asks the replica to stop a run at its next node boundary.
func (c *Client) Cancel(ctx context.Context, baseURL, runID, reason string) (bool, error) {
	var out cancelAccepted
	status, err := c.do(ctx, c.executeTimeout, http.MethodPost,
		baseURL+fmt.Sprintf(PathCancel, runID), cancelRequest{Reason: reason}, &out)
	if err != nil {
		return false, err
	}
	if status != http.StatusAccepted {
		return false, fmt.Errorf("%w: cancel status %d", ErrUnreachable, status)
	}
	return out.WasRunning, nil
}

func (c *Client) do(ctx context.Context, timeout time.Duration, method, url string, payload, out any) (int, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	var body io.Reader
	if payload != nil {
		encoded, err := json.Marshal(payload)
		if err != nil {
			return 0, fmt.Errorf("encode request: %w", err)
		}
		body = bytes.NewReader(encoded)
	}
	request, err := http.NewRequestWithContext(ctx, method, url, body)
	if err != nil {
		return 0, fmt.Errorf("%w: %v", ErrUnreachable, err)
	}
	if payload != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	request.Header.Set("Accept", "application/json")

	response, err := c.http.Do(request)
	if err != nil {
		return 0, fmt.Errorf("%w: %v", ErrUnreachable, err)
	}
	defer response.Body.Close()

	raw, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil {
		return response.StatusCode, fmt.Errorf("%w: read body: %v", ErrUnreachable, err)
	}
	if out != nil && len(raw) > 0 {
		// A body that does not decode is not fatal for error statuses; the status code decides.
		if err := json.Unmarshal(raw, out); err != nil && response.StatusCode < 300 {
			return response.StatusCode, fmt.Errorf("decode %s: %w", url, err)
		}
	}
	return response.StatusCode, nil
}
