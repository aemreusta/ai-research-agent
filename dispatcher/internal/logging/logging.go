// Package logging configures slog to emit the same JSON field names as the Python services
// (structlog), so one query finds a run across both languages: service, run_id, node, event,
// error_code (architecture v0.6 §14).
package logging

import (
	"io"
	"log/slog"
	"strings"
)

// New returns a JSON logger tagged with the service name.
func New(w io.Writer, level string) *slog.Logger {
	handler := slog.NewJSONHandler(w, &slog.HandlerOptions{
		Level: parseLevel(level),
		ReplaceAttr: func(_ []string, attr slog.Attr) slog.Attr {
			switch attr.Key {
			case slog.MessageKey:
				attr.Key = "event" // structlog's name for the message
			case slog.TimeKey:
				attr.Key = "timestamp"
			case slog.LevelKey:
				attr.Value = slog.StringValue(strings.ToLower(attr.Value.String()))
			}
			return attr
		},
	})
	return slog.New(handler).With("service", "dispatcher")
}

func parseLevel(level string) slog.Level {
	switch strings.ToUpper(level) {
	case "DEBUG":
		return slog.LevelDebug
	case "WARNING", "WARN":
		return slog.LevelWarn
	case "ERROR", "CRITICAL":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}
