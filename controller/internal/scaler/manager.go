package scaler

import (
	"context"
	"fmt"

	appsv1 "k8s.io/api/apps/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	scaleActionsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "phantom_scale_actions_total",
		Help: "Total number of pre-scale actions taken by PHANTOM",
	}, []string{"deployment", "direction"})

	replicasGauge = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Name: "phantom_target_replicas",
		Help: "Target replica count set by PHANTOM",
	}, []string{"deployment"})

	confidenceGauge = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Name: "phantom_prediction_confidence",
		Help: "Model confidence for the last prediction",
	}, []string{"deployment"})

	predictionErrorGauge = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Name: "phantom_prediction_mape",
		Help: "Mean Absolute Percentage Error of recent predictions",
	}, []string{"deployment"})
)

// Manager applies pre-computed replica counts to Kubernetes deployments
type Manager struct {
	client client.Client
}

func NewManager(c client.Client) *Manager {
	return &Manager{client: c}
}

func (m *Manager) SetReplicas(ctx context.Context, deploy *appsv1.Deployment, desired int32) error {
	current := int32(1)
	if deploy.Spec.Replicas != nil {
		current = *deploy.Spec.Replicas
	}

	direction := "up"
	if desired < current {
		direction = "down"
	}

	patch := client.MergeFrom(deploy.DeepCopy())
	deploy.Spec.Replicas = &desired

	if err := m.client.Patch(ctx, deploy, patch); err != nil {
		return fmt.Errorf("patch deployment %s/%s: %w", deploy.Namespace, deploy.Name, err)
	}

	// Record metrics
	scaleActionsTotal.WithLabelValues(deploy.Name, direction).Inc()
	replicasGauge.WithLabelValues(deploy.Name).Set(float64(desired))

	return nil
}

func (m *Manager) RecordConfidence(deployment string, confidence float64) {
	confidenceGauge.WithLabelValues(deployment).Set(confidence)
}

func (m *Manager) RecordMAPE(deployment string, mape float64) {
	predictionErrorGauge.WithLabelValues(deployment).Set(mape)
}
