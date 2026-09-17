package dispatcher

import (
	"context"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Channels the dispatcher listens on. A queued run wakes the dispatch loop; a status change can
// free a slot, which is also a reason to look at the queue again.
var listenChannels = []string{"run_queued", "run_events"}

// ListenLoop holds a dedicated connection with LISTEN and pokes Wake on every notification.
// Losing the connection is not fatal: the dispatch loop still polls, and this loop reconnects.
func (d *Dispatcher) ListenLoop(ctx context.Context, pool *pgxpool.Pool) {
	d.init()
	backoff := time.Second
	for ctx.Err() == nil {
		err := d.listenOnce(ctx, pool)
		if ctx.Err() != nil {
			return
		}
		d.Log.Warn("notification listener dropped; polling covers the gap", "error", err, "retry_in", backoff.String())
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		backoff = min(backoff*2, 30*time.Second)
	}
}

func (d *Dispatcher) listenOnce(ctx context.Context, pool *pgxpool.Pool) error {
	pooled, err := pool.Acquire(ctx)
	if err != nil {
		return err
	}
	// A connection that has run LISTEN must never go back to the pool, so it is taken out of
	// the pool's ownership before first use and closed by us.
	conn := pooled.Hijack()
	defer conn.Close(context.Background())

	for _, channel := range listenChannels {
		if _, err := conn.Exec(ctx, "LISTEN "+channel); err != nil {
			return err
		}
	}
	d.Log.Info("listening for notifications", "channels", listenChannels)
	d.Wake() // Notifications sent during reconnect were lost; reconcile the queue immediately.
	for {
		notification, err := conn.WaitForNotification(ctx)
		if err != nil {
			return err
		}
		// Every event would be too chatty to act on individually; any notification is just a
		// hint that the queue or capacity may have changed.
		if notification.Channel == "run_queued" || !isPlainEvent(notification.Payload) {
			d.Wake()
		}
	}
}

// isPlainEvent is true for `{"run_id", "seq"}` payloads (a new timeline entry), which never
// change capacity; status changes carry a "status" key and do.
func isPlainEvent(payload string) bool {
	return payload != "" && !strings.Contains(payload, `"status"`)
}
