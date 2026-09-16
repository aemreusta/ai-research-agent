package dispatcher

import (
	"context"
	"io"
	"log/slog"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// A queued-run notification must wake the dispatch loop within moments, not at the next poll.
func TestANotificationWakesTheDispatchLoop(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("set TEST_DATABASE_URL to run listener integration tests")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()

	d := &Dispatcher{Log: slog.New(slog.NewTextHandler(io.Discard, nil))}
	d.init()
	listening, stop := context.WithCancel(ctx)
	defer stop()
	go d.ListenLoop(listening, pool)

	// Keep notifying until the listener is up; the first few may precede its LISTEN.
	deadline := time.After(5 * time.Second)
	for {
		if _, err := pool.Exec(ctx, `SELECT pg_notify('run_queued', '{"run_id":"x"}')`); err != nil {
			t.Fatal(err)
		}
		select {
		case <-d.wake:
			return
		case <-deadline:
			t.Fatal("the listener never woke the dispatch loop")
		case <-time.After(100 * time.Millisecond):
		}
	}
}
