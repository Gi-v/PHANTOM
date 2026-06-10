package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// PredictiveScalerSpec defines the desired state
type PredictiveScalerSpec struct {
	// TargetDeployment is the deployment to scale
	TargetDeployment string `json:"targetDeployment"`
	// MinReplicas is the lower bound
	// +kubebuilder:default=1
	MinReplicas int32 `json:"minReplicas,omitempty"`
	// MaxReplicas is the upper bound
	MaxReplicas int32 `json:"maxReplicas"`
	// PredictionHorizonSeconds: how far ahead to predict (default 300 = 5min)
	// +kubebuilder:default=300
	PredictionHorizonSeconds int32 `json:"predictionHorizonSeconds,omitempty"`
	// ConfidenceThreshold: minimum model confidence to act (0-1)
	// +kubebuilder:default="0.75"
	ConfidenceThreshold string `json:"confidenceThreshold,omitempty"`
	// ScaleUpBuffer: over-provision multiplier (e.g. 1.2 = 20% headroom)
	// +kubebuilder:default="1.2"
	ScaleUpBuffer string `json:"scaleUpBuffer,omitempty"`
}

// ScalerPhase describes the current phase
type ScalerPhase string

const (
	PhaseIdle       ScalerPhase = "Idle"
	PhasePredicting ScalerPhase = "Predicting"
	PhaseScaling    ScalerPhase = "Scaling"
	PhaseStable     ScalerPhase = "Stable"
	PhaseError      ScalerPhase = "Error"
)

// PredictiveScalerStatus defines the observed state
type PredictiveScalerStatus struct {
	CurrentReplicas   int32       `json:"currentReplicas,omitempty"`
	PredictedReplicas int32       `json:"predictedReplicas,omitempty"`
	ModelConfidence   float64     `json:"modelConfidence,omitempty"`
	LastPrediction    metav1.Time `json:"lastPrediction,omitempty"`
	LastScaleAction   metav1.Time `json:"lastScaleAction,omitempty"`
	Phase             ScalerPhase `json:"phase,omitempty"`
	Message           string      `json:"message,omitempty"`
	// PredictionAccuracy: MAPE over last 10 predictions
	PredictionAccuracy float64 `json:"predictionAccuracy,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Target",type=string,JSONPath=`.spec.targetDeployment`
// +kubebuilder:printcolumn:name="Current",type=integer,JSONPath=`.status.currentReplicas`
// +kubebuilder:printcolumn:name="Predicted",type=integer,JSONPath=`.status.predictedReplicas`
// +kubebuilder:printcolumn:name="Confidence",type=number,JSONPath=`.status.modelConfidence`
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=`.status.phase`

// PredictiveScaler is the Schema for the predictivescalers API
type PredictiveScaler struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`
	Spec              PredictiveScalerSpec   `json:"spec,omitempty"`
	Status            PredictiveScalerStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true
type PredictiveScalerList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []PredictiveScaler `json:"items"`
}

func init() {
	SchemeBuilder.Register(&PredictiveScaler{}, &PredictiveScalerList{})
}
