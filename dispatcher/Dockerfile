# syntax=docker/dockerfile:1.7
# The control plane: a single static binary on distroless. No shell, no package manager, no
# secrets - it only ever needs the database and the agents (architecture v0.6 §2).
# Build context is the repository root, so config/ and contracts/ can be copied in.

FROM golang:1.27-alpine AS builder
WORKDIR /src
COPY dispatcher/go.mod dispatcher/go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY dispatcher/ ./
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/dispatcher ./cmd/dispatcher


FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /app
COPY --from=builder /out/dispatcher /app/dispatcher
COPY config/dispatcher.yaml /app/config/dispatcher.yaml
COPY contracts/run_states.yaml contracts/error_codes.yaml /app/contracts/
ENV APP_CONFIG_DIR=/app/config \
    APP_CONTRACTS_DIR=/app/contracts
EXPOSE 8090
USER nonroot:nonroot
ENTRYPOINT ["/app/dispatcher"]
