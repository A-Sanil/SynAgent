#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# SLURM job script — XGBoost yield predictor on Savio (UC Berkeley HPC)
#
# Partition guide:
#   savio2      → 24 cores/node, 64 GB RAM, free tier  ← DEFAULT (no GPU needed)
#   savio3      → 32 cores/node, 96 GB RAM
#   savio2_gpu  → 8 cores + 2x GTX 1080Ti  (if you want GPU-boosted XGB)
#   savio4_gpu  → 32 cores + 4x A40        (fastest GPU option)
#
# Submit:
#   sbatch savio_xgb.sh
#
# Check status:
#   squeue -u $USER
#   tail -f logs/xgb_yield_%j.out
# ─────────────────────────────────────────────────────────────────────────────

#SBATCH --job-name=synagent_xgb
#SBATCH --account=fc_synagent          # ← CHANGE to your Savio account (check: sacctmgr show assoc user=$USER)
# CPU-only partition (default — no GPU needed for XGBoost, still fast with 24 cores)
#SBATCH --partition=savio2
#SBATCH --cpus-per-task=24
#SBATCH --mem=60G
#
# GPU partition — uncomment below + comment out the two lines above to use GPU
# (savio3_gpu: GTX 1080Ti  |  savio4_gpu: A40 — much faster)
##SBATCH --partition=savio4_gpu
##SBATCH --cpus-per-task=4
##SBATCH --gres=gpu:1
##SBATCH --mem=32G
#
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=02:00:00               # XGBoost on 2M reactions should finish in <1 hr
#SBATCH --output=logs/xgb_yield_%j.out
#SBATCH --error=logs/xgb_yield_%j.err
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=YOUR_EMAIL@berkeley.edu   # ← CHANGE

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRATCH="/global/scratch/users/$USER/synagent"
DB_ORD="$SCRATCH/ord_full.db"
DB_PATENT="$SCRATCH/patent_pipeline.db"
MODEL_DIR="$SCRATCH/models"
CODE_DIR="$SCRATCH/code"

mkdir -p "$MODEL_DIR" logs

# ── Environment ───────────────────────────────────────────────────────────────
module load python/3.11
source "$SCRATCH/env/bin/activate"

# ── Tell XGBoost to use all allocated cores ────────────────────────────────
export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK

echo "============================================"
echo "  SynAgent XGBoost — Savio"
echo "  Job:  $SLURM_JOB_ID"
echo "  Node: $SLURMD_NODENAME"
echo "  CPUs: $SLURM_CPUS_PER_TASK"
echo "  Time: $(date)"
echo "============================================"

# ── Run training ──────────────────────────────────────────────────────────────
# Detect if a GPU was allocated and add --gpu flag automatically
GPU_FLAG=""
if [ ! -z "$CUDA_VISIBLE_DEVICES" ] && [ "$CUDA_VISIBLE_DEVICES" != "" ]; then
    GPU_FLAG="--gpu"
    echo "GPU detected: $CUDA_VISIBLE_DEVICES"
fi

python "$CODE_DIR/mainTorch.py" \
    --db        "$DB_ORD" \
    --extra_db  "$DB_PATENT" \
    --model_dir "$MODEL_DIR" \
    --n_jobs    $SLURM_CPUS_PER_TASK \
    --optuna \
    $GPU_FLAG

echo "Done at $(date)"
