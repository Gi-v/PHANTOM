package controller

import (
	"context"
	"fmt"
	"strconv"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"

	phantomv1alpha1 "github.com/phantom-io/phantom/api/v1alpha1"
	"github.com/phantom-io/phantom/internal/predictor"
	"github.com/phantom-io/phantom/internal/scaler"
)

const (
	reconcileInterval = 30 * time.Second
	cooldownPeriod    = 2 * time.Minute
)

// PredictiveScalerReconciler reconciles PredictiveScaler objects.
type PredictiveScalerReconciler struct {
	client.Client
	Scheme    *runtime.Scheme
	Predictor *predictor.Client
	Scaler    *scaler.Manager
}

func (r *PredictiveScalerReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	// Fetch the PredictiveScaler
	ps := &phantomv1alpha1.PredictiveScaler{}
	if err := r.Get(ctx, req.NamespacedName, ps); err != nil {
		if errors.IsNotFound(err) {
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, err
	}

	// Parse config with safe defaults
	confidenceThreshold, err := strconv.ParseFloat(ps.Spec.ConfidenceThreshold, 64)
	if err != nil || confidenceThreshold <= 0 {
		confidenceThreshold = 0.75
	}
	scaleUpBuffer, err := strconv.ParseFloat(ps.Spec.ScaleUpBuffer, 64)
	if err != nil || scaleUpBuffer <= 0 {
		scaleUpBuffer = 1.2
	}

	// Capture original for status patch
	original := ps.DeepCopy()

	// Fetch target deployment
	deploy := &appsv1.Deployment{}
	if err := r.Get(ctx, types.NamespacedName{
		Name:      ps.Spec.TargetDeployment,
		Namespace: req.Namespace,
	}, deploy); err != nil {
		ps.Status.Phase = phantomv1alpha1.PhaseError
		ps.Status.Message = fmt.Sprintf("deployment not found: %s", ps.Spec.TargetDeployment)
		_ = r.Status().Patch(ctx, ps, client.MergeFrom(original))
		return ctrl.Result{RequeueAfter: reconcileInterval}, nil
	}

	currentReplicas := int32(1)
	if deploy.Spec.Replicas != nil {
		currentReplicas = *deploy.Spec.Replicas
	}

	// Query ML service
	prediction, err := r.Predictor.Predict(ctx, predictor.PredictRequest{
		ServiceName:    ps.Spec.TargetDeployment,
		Namespace:      req.Namespace,
		HorizonSeconds: ps.Spec.PredictionHorizonSeconds,
	})
	if err != nil {
		logger.Error(err, "prediction failed, HPA will remain in control")
		ps.Status.Phase = phantomv1alpha1.PhaseError
		ps.Status.Message = fmt.Sprintf("prediction failed: %v", err)
		ps.Status.CurrentReplicas = currentReplicas
		_ = r.Status().Patch(ctx, ps, client.MergeFrom(original))
		return ctrl.Result{RequeueAfter: reconcileInterval}, nil
	}

	r.Scaler.RecordConfidence(ps.Spec.TargetDeployment, prediction.Confidence)

	// Confidence gate — fall back to HPA if model isn't confident
	if prediction.Confidence < confidenceThreshold {
		logger.Info("confidence below threshold, HPA in control",
			"confidence", prediction.Confidence, "threshold", confidenceThreshold)
		ps.Status.Phase = phantomv1alpha1.PhaseStable
		ps.Status.Message = fmt.Sprintf("confidence %.2f < %.2f — HPA in control",
			prediction.Confidence, confidenceThreshold)
		ps.Status.ModelConfidence = prediction.Confidence
		ps.Status.CurrentReplicas = currentReplicas
		_ = r.Status().Patch(ctx, ps, client.MergeFrom(original))
		return ctrl.Result{RequeueAfter: reconcileInterval}, nil
	}

	// Compute desired replicas
	desiredReplicas := r.computeReplicas(prediction, ps.Spec, scaleUpBuffer)

	// Cooldown: don't scale down within cooldown window
	if !ps.Status.LastScaleAction.IsZero() {
		elapsed := time.Since(ps.Status.LastScaleAction.Time)
		if elapsed < cooldownPeriod && desiredReplicas < currentReplicas {
			logger.Info("cooldown active, skipping scale-down",
				"elapsed", elapsed.Round(time.Second))
			ps.Status.Phase = phantomv1alpha1.PhaseStable
			ps.Status.Message = fmt.Sprintf("cooldown active (%.0fs remaining)",
				(cooldownPeriod - elapsed).Seconds())
			ps.Status.CurrentReplicas = currentReplicas
			ps.Status.PredictedReplicas = desiredReplicas
			_ = r.Status().Patch(ctx, ps, client.MergeFrom(original))
			return ctrl.Result{RequeueAfter: reconcileInterval}, nil
		}
	}

	// Apply scaling if needed
	if desiredReplicas != currentReplicas {
		logger.Info("pre-scaling deployment",
			"deployment", ps.Spec.TargetDeployment,
			"current", currentReplicas,
			"desired", desiredReplicas,
			"confidence", prediction.Confidence)

		if err := r.Scaler.SetReplicas(ctx, deploy, desiredReplicas); err != nil {
			ps.Status.Phase = phantomv1alpha1.PhaseError
			ps.Status.Message = fmt.Sprintf("scale failed: %v", err)
			_ = r.Status().Patch(ctx, ps, client.MergeFrom(original))
			return ctrl.Result{RequeueAfter: reconcileInterval}, nil
		}
		ps.Status.LastScaleAction = metav1.Now()
		ps.Status.Phase = phantomv1alpha1.PhaseScaling
	} else {
		ps.Status.Phase = phantomv1alpha1.PhaseStable
	}

	// Final status update (single patch — no resource version conflict)
	ps.Status.CurrentReplicas = currentReplicas
	ps.Status.PredictedReplicas = desiredReplicas
	ps.Status.ModelConfidence = prediction.Confidence
	ps.Status.LastPrediction = metav1.Now()
	ps.Status.Message = fmt.Sprintf("predicted %.0f RPS → %d replicas (conf %.2f)",
		prediction.PredictedRPS, desiredReplicas, prediction.Confidence)

	if err := r.Status().Patch(ctx, ps, client.MergeFrom(original)); err != nil {
		logger.Error(err, "failed to update status")
	}

	return ctrl.Result{RequeueAfter: reconcileInterval}, nil
}

func (r *PredictiveScalerReconciler) computeReplicas(
	pred *predictor.PredictResponse,
	spec phantomv1alpha1.PredictiveScalerSpec,
	buffer float64,
) int32 {
	rpsPerReplica := pred.RPSPerReplica
	if rpsPerReplica <= 0 {
		rpsPerReplica = 100
	}
	raw := (pred.PredictedRPS / rpsPerReplica) * buffer
	desired := int32(raw) + 1
	if desired < spec.MinReplicas {
		desired = spec.MinReplicas
	}
	if desired > spec.MaxReplicas {
		desired = spec.MaxReplicas
	}
	return desired
}

func (r *PredictiveScalerReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&phantomv1alpha1.PredictiveScaler{}).
		Complete(r)
}
