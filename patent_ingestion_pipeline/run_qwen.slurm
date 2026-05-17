#!/bin/bash
#SBATCH --job-name=qwen_server
#SBATCH --partition=savio3_gpu
#SBATCH --account=fc_fishes
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:1
#SBATCH --time=04:00:00
#SBATCH --output=slurm-%j.out
#SBATCH --export=ALL

set -euo pipefail

MODEL_PATH=/global/scratch/users/asanil/models/Qwen3.6-27B

if [ ! -d "$MODEL_PATH" ]; then
  echo "Model path not found: $MODEL_PATH"
  exit 1
fi

echo "=================================================="
echo "Starting vLLM on compute node: $(hostname -s)"
echo "Model path: $MODEL_PATH"
echo "=================================================="

module load apptainer

apptainer exec --nv \
  --bind /global/scratch/users/asanil:/global/scratch/users/asanil \
  docker://vllm/vllm-openai:latest \
  python3 -m vllm.entrypoints.openai.api_server \
    --model "$MODEL_PATH" \
    --served-model-name qwen \
    --host 0.0.0.0 \
    --port 8000 \
    --max-model-len 32768 \
    --dtype bfloat16
