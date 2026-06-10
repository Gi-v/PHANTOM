package predictor

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

type PredictRequest struct {
	ServiceName    string `json:"service_name"`
	Namespace      string `json:"namespace"`
	HorizonSeconds int32  `json:"horizon_seconds"`
}

type PredictResponse struct {
	ServiceName   string  `json:"service_name"`
	PredictedRPS  float64 `json:"predicted_rps"`
	Confidence    float64 `json:"confidence"`
	RPSPerReplica float64 `json:"rps_per_replica"`
	ModelVersion  string  `json:"model_version"`
	GeneratedAt   string  `json:"generated_at"`
}

type Client struct {
	baseURL    string
	httpClient *http.Client
}

func NewClient(baseURL string) *Client {
	return &Client{
		baseURL: baseURL,
		httpClient: &http.Client{
			Timeout: 10 * time.Second,
		},
	}
}

func (c *Client) Predict(ctx context.Context, req PredictRequest) (*PredictResponse, error) {
	url := fmt.Sprintf("%s/predict/%s?namespace=%s&horizon=%d",
		c.baseURL, req.ServiceName, req.Namespace, req.HorizonSeconds)

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("ml service unreachable: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("ml service returned %d", resp.StatusCode)
	}

	var result PredictResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode response: %w", err)
	}

	return &result, nil
}

func (c *Client) Health(ctx context.Context) error {
	url := fmt.Sprintf("%s/health", c.baseURL)
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("unhealthy: %d", resp.StatusCode)
	}
	return nil
}
