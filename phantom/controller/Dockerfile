FROM golang:1.22-alpine AS builder
WORKDIR /app

# Install dependencies first (cached layer)
COPY go.mod ./
# go.sum will be generated; in CI, run `go mod tidy` before building
RUN go mod download || true

COPY . .
RUN go mod tidy && \
    CGO_ENABLED=0 GOOS=linux go build -a -ldflags="-w -s" -o controller ./cmd/controller

FROM gcr.io/distroless/static:nonroot
COPY --from=builder /app/controller /controller
USER 65532:65532
ENTRYPOINT ["/controller"]
