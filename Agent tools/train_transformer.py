"""
SynAgent Reaction Transformer — yield prediction from reaction SMARTS.

Architecture:
  - Atom-level SMILES tokenizer  (handles reaction SMARTS with >> separator)
  - BERT-style encoder (6 layers, 256-dim, 8 heads) trained from scratch
  - [CLS] pooling → regression head → yield (0–100 %)

Can also use a pre-trained backbone (ChemBERTa) via --backbone flag.

Usage:
    # From scratch (default, recommended for NERSC):
    python "Agent tools/train_transformer.py" \
        --db D:\\SynAgent\\db\\ord_full.db \
        --extra_db D:\\SynAgent\\db\\patent_pipeline.db

    # Using ChemBERTa backbone (faster, needs internet once to download):
    python "Agent tools/train_transformer.py" --backbone chemberta

    # NERSC Perlmutter (multi-GPU):
    torchrun --nproc_per_node=4 "Agent tools/train_transformer.py" --distributed

Requires:
    pip install torch transformers datasets scikit-learn numpy tqdm
"""

import argparse
import json
import math
import os
import re
import sqlite3
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from tqdm import tqdm

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_DB   = r"D:\SynAgent\db\ord_full.db"
PATENT_DB    = r"D:\SynAgent\db\patent_pipeline.db"
MODEL_DIR    = r"D:\SynAgent\models"

MIN_YIELD    = 5.0
MAX_YIELD    = 100.0
MAX_SEQ_LEN  = 512      # max tokens per reaction SMARTS
RANDOM_SEED  = 42

# Scratch transformer hyperparams (tune on NERSC)
D_MODEL      = 256      # hidden dim
N_HEADS      = 8        # attention heads
N_LAYERS     = 6        # encoder layers
D_FF         = 1024     # feed-forward dim
DROPOUT      = 0.1

# Training
BATCH_SIZE   = 128
EPOCHS       = 50
LR           = 3e-4     # from-scratch LR (higher than fine-tuning)
WARMUP_STEPS = 1000
WEIGHT_DECAY = 0.01


# ═══════════════════════════════════════════════════════════════════
# 1. SMILES TOKENIZER
#    Handles multi-char atoms (Cl, Br, Si, Se) and reaction SMARTS
#    with >> separator and . (dot) for multiple reactants.
# ═══════════════════════════════════════════════════════════════════

# Regex: bracketed atoms first, then multi-char atoms, then singles
_SMILES_RE = re.compile(
    r"(\[[^\]]+\]"     # bracketed atom/group e.g. [NH3+]
    r"|Br|Cl|Si|Se|se|br|cl"  # multi-char atoms
    r"|@@|@"           # stereo
    r"|>>|\.{2,}"      # reaction arrow, bond dots
    r"|[BCFINOPSbcnosp\d%\-=#$:/\\+\(\)\.])"  # everything else
)

SPECIAL_TOKENS = {
    "[PAD]": 0,
    "[CLS]": 1,
    "[SEP]": 2,
    "[MASK]": 3,
    "[UNK]": 4,
}


def tokenize_smarts(smarts: str) -> list[str]:
    """Split a reaction SMARTS string into atom-level tokens."""
    # Replace >> with a special token so it survives the regex
    smarts = smarts.replace(">>", " [RXNARROW] ")
    tokens = _SMILES_RE.findall(smarts)
    # Restore reaction arrow token
    tokens = ["[RXNARROW]" if t.strip() == "[RXNARROW]" else t for t in tokens]
    tokens = [t for t in tokens if t.strip()]
    return tokens


def build_vocab(reactions: list[str], extra_tokens: list[str] = None) -> dict[str, int]:
    """Build vocabulary from a list of reaction SMARTS strings."""
    vocab = dict(SPECIAL_TOKENS)
    token_set = set()
    for smarts in reactions:
        token_set.update(tokenize_smarts(smarts))
    # Add reaction arrow and any extra tokens
    token_set.add("[RXNARROW]")
    if extra_tokens:
        token_set.update(extra_tokens)
    for tok in sorted(token_set):
        if tok not in vocab:
            vocab[tok] = len(vocab)
    return vocab


class SmilesTokenizer:
    def __init__(self, vocab: dict[str, int], max_len: int = MAX_SEQ_LEN):
        self.vocab   = vocab
        self.max_len = max_len
        self.pad_id  = vocab["[PAD]"]
        self.cls_id  = vocab["[CLS]"]
        self.unk_id  = vocab["[UNK]"]

    def encode(self, smarts: str) -> dict[str, torch.Tensor]:
        tokens = tokenize_smarts(smarts)[: self.max_len - 2]   # leave room for [CLS] + [SEP]
        ids    = [self.cls_id] + [self.vocab.get(t, self.unk_id) for t in tokens] + [self.vocab["[SEP]"]]
        pad_len = self.max_len - len(ids)
        mask   = [1] * len(ids) + [0] * pad_len
        ids   += [self.pad_id] * pad_len
        return {
            "input_ids":      torch.tensor(ids,  dtype=torch.long),
            "attention_mask": torch.tensor(mask, dtype=torch.long),
        }

    def save(self, path: str):
        with open(path, "w") as f:
            json.dump({"vocab": self.vocab, "max_len": self.max_len}, f)

    @classmethod
    def load(cls, path: str) -> "SmilesTokenizer":
        with open(path) as f:
            d = json.load(f)
        return cls(d["vocab"], d["max_len"])


# ═══════════════════════════════════════════════════════════════════
# 2. MODEL ARCHITECTURE
# ═══════════════════════════════════════════════════════════════════

class ReactionTransformer(nn.Module):
    """
    BERT-style encoder trained from scratch on reaction SMARTS strings.

    Input:  tokenized reaction SMARTS (batch of [CLS] token sequences)
    Output: scalar yield prediction (0–100 %)

    [CLS] → TransformerEncoder (N layers) → Linear head → yield
    """

    def __init__(self, vocab_size: int, d_model: int = D_MODEL,
                 n_heads: int = N_HEADS, n_layers: int = N_LAYERS,
                 d_ff: int = D_FF, dropout: float = DROPOUT,
                 max_len: int = MAX_SEQ_LEN):
        super().__init__()

        self.token_emb = nn.Embedding(vocab_size, d_model, padding_idx=0)
        self.pos_emb   = nn.Embedding(max_len, d_model)
        self.emb_norm  = nn.LayerNorm(d_model)
        self.emb_drop  = nn.Dropout(dropout)

        encoder_layer  = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=n_heads, dim_feedforward=d_ff,
            dropout=dropout, batch_first=True, norm_first=True  # Pre-LN (more stable)
        )
        self.encoder   = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

        # Regression head:  [CLS] embedding → yield
        self.head = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(d_model // 2, 1),
        )

    def forward(self, input_ids: torch.Tensor,
                attention_mask: torch.Tensor) -> torch.Tensor:
        B, L = input_ids.shape
        pos  = torch.arange(L, device=input_ids.device).unsqueeze(0)  # (1, L)

        x = self.token_emb(input_ids) + self.pos_emb(pos)
        x = self.emb_norm(x)
        x = self.emb_drop(x)

        # Convert attention mask: 1 = attend, 0 = ignore → True = ignore (PyTorch convention)
        key_padding_mask = attention_mask == 0   # (B, L), True = padded

        x = self.encoder(x, src_key_padding_mask=key_padding_mask)

        # [CLS] token is index 0
        cls_out = x[:, 0, :]       # (B, d_model)
        yield_pred = self.head(cls_out).squeeze(-1)   # (B,)
        return yield_pred


# ═══════════════════════════════════════════════════════════════════
# 2b. OPTIONAL: ChemBERTa backbone (--backbone chemberta)
# ═══════════════════════════════════════════════════════════════════

class ChemBERTaYieldModel(nn.Module):
    """
    Uses a pre-trained ChemBERTa encoder backbone + custom regression head.
    Loads from HuggingFace: seyonec/ChemBERTa-zinc-base-v1
    """

    def __init__(self, dropout: float = DROPOUT):
        super().__init__()
        from transformers import AutoModel
        self.backbone = AutoModel.from_pretrained("seyonec/ChemBERTa-zinc-base-v1")
        hidden = self.backbone.config.hidden_size  # typically 768
        self.head = nn.Sequential(
            nn.Linear(hidden, 256),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(256, 1),
        )

    def forward(self, input_ids, attention_mask):
        out = self.backbone(input_ids=input_ids, attention_mask=attention_mask)
        cls = out.last_hidden_state[:, 0, :]  # [CLS]
        return self.head(cls).squeeze(-1)


# ═══════════════════════════════════════════════════════════════════
# 3. DATASET
# ═══════════════════════════════════════════════════════════════════

class ReactionDataset(Dataset):
    def __init__(self, smarts: list[str], yields: list[float],
                 tokenizer: SmilesTokenizer):
        self.smarts    = smarts
        self.yields    = yields
        self.tokenizer = tokenizer

    def __len__(self):
        return len(self.smarts)

    def __getitem__(self, idx):
        enc = self.tokenizer.encode(self.smarts[idx])
        return {
            "input_ids":      enc["input_ids"],
            "attention_mask": enc["attention_mask"],
            "yield":          torch.tensor(self.yields[idx], dtype=torch.float32),
        }


# ═══════════════════════════════════════════════════════════════════
# 4. DATA LOADING
# ═══════════════════════════════════════════════════════════════════

def load_reactions(db_path: str) -> tuple[list[str], list[float]]:
    if not os.path.exists(db_path):
        print(f"  [SKIP] {db_path} not found")
        return [], []
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    cur.execute(
        f"SELECT reaction_smarts, yield_percent FROM reactions "
        f"WHERE reaction_smarts IS NOT NULL "
        f"  AND reaction_smarts LIKE '%>>%' "
        f"  AND yield_percent BETWEEN {MIN_YIELD} AND {MAX_YIELD}"
    )
    rows = cur.fetchall()
    conn.close()
    smarts = [r[0] for r in rows]
    yields = [float(r[1]) for r in rows]
    print(f"  Loaded {len(rows):,} from {db_path}")
    return smarts, yields


# ═══════════════════════════════════════════════════════════════════
# 5. TRAINING UTILITIES
# ═══════════════════════════════════════════════════════════════════

def get_cosine_schedule_with_warmup(optimizer, warmup_steps, total_steps):
    def lr_lambda(current_step):
        if current_step < warmup_steps:
            return float(current_step) / max(1, warmup_steps)
        progress = (current_step - warmup_steps) / max(1, total_steps - warmup_steps)
        return max(0.0, 0.5 * (1.0 + math.cos(math.pi * progress)))
    from torch.optim.lr_scheduler import LambdaLR
    return LambdaLR(optimizer, lr_lambda)


def evaluate(model, loader, device):
    model.eval()
    preds_all, targets_all = [], []
    with torch.no_grad():
        for batch in loader:
            ids  = batch["input_ids"].to(device)
            mask = batch["attention_mask"].to(device)
            y    = batch["yield"].numpy()
            out  = model(ids, mask).cpu().numpy()
            preds_all.extend(out.clip(0, 100).tolist())
            targets_all.extend(y.tolist())
    preds   = np.array(preds_all)
    targets = np.array(targets_all)
    rmse = math.sqrt(mean_squared_error(targets, preds))
    mae  = mean_absolute_error(targets, preds)
    r2   = r2_score(targets, preds)
    return rmse, mae, r2


# ═══════════════════════════════════════════════════════════════════
# 6. MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",          default=DEFAULT_DB)
    parser.add_argument("--extra_db",    default=None)
    parser.add_argument("--backbone",    default="scratch",
                        choices=["scratch", "chemberta"],
                        help="'scratch' = train from scratch | 'chemberta' = ChemBERTa backbone")
    parser.add_argument("--epochs",      type=int, default=EPOCHS)
    parser.add_argument("--batch_size",  type=int, default=BATCH_SIZE)
    parser.add_argument("--lr",          type=float, default=LR)
    parser.add_argument("--distributed", action="store_true",
                        help="Use torch.distributed (torchrun on NERSC)")
    args = parser.parse_args()

    torch.manual_seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)

    # ── Distributed setup (NERSC) ─────────────────────────────────────────
    distributed = args.distributed and torch.cuda.device_count() > 1
    if distributed:
        torch.distributed.init_process_group(backend="nccl")
        local_rank = int(os.environ.get("LOCAL_RANK", 0))
        device = torch.device(f"cuda:{local_rank}")
        torch.cuda.set_device(device)
    else:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        local_rank = 0

    is_main = local_rank == 0

    if is_main:
        print("=" * 60)
        print("  SynAgent — Reaction Transformer Yield Predictor")
        print(f"  Backbone: {args.backbone}  |  Device: {device}")
        print("=" * 60)

    # ── Load data ─────────────────────────────────────────────────────────
    if is_main:
        print("\nLoading reactions...")
    smarts, yields = load_reactions(args.db)
    if args.extra_db:
        s2, y2 = load_reactions(args.extra_db)
        smarts += s2
        yields += y2
    if not smarts:
        print("No data — check DB paths.")
        return
    if is_main:
        print(f"  Total: {len(smarts):,} reactions")

    # ── Train / val / test split ──────────────────────────────────────────
    s_tmp, s_test, y_tmp, y_test = train_test_split(
        smarts, yields, test_size=0.10, random_state=RANDOM_SEED)
    s_train, s_val, y_train, y_val = train_test_split(
        s_tmp, y_tmp, test_size=0.111, random_state=RANDOM_SEED)  # 0.111 ≈ 10% of total
    if is_main:
        print(f"  Train: {len(s_train):,}  Val: {len(s_val):,}  Test: {len(s_test):,}")

    # ── Tokenizer ─────────────────────────────────────────────────────────
    os.makedirs(MODEL_DIR, exist_ok=True)
    tok_path = os.path.join(MODEL_DIR, "smiles_tokenizer.json")

    if args.backbone == "scratch":
        if is_main:
            print("\nBuilding vocabulary from training set...")
        vocab     = build_vocab(s_train)
        tokenizer = SmilesTokenizer(vocab, max_len=MAX_SEQ_LEN)
        if is_main:
            tokenizer.save(tok_path)
            print(f"  Vocab size: {len(vocab)}  |  Saved to {tok_path}")

        train_ds = ReactionDataset(s_train, y_train, tokenizer)
        val_ds   = ReactionDataset(s_val,   y_val,   tokenizer)
        test_ds  = ReactionDataset(s_test,  y_test,  tokenizer)

        model = ReactionTransformer(
            vocab_size=len(vocab),
            d_model=D_MODEL, n_heads=N_HEADS, n_layers=N_LAYERS,
            d_ff=D_FF, dropout=DROPOUT, max_len=MAX_SEQ_LEN,
        )

    else:  # ChemBERTa backbone
        from transformers import AutoTokenizer
        if is_main:
            print("\nLoading ChemBERTa tokenizer...")
        hf_tok = AutoTokenizer.from_pretrained("seyonec/ChemBERTa-zinc-base-v1")

        class HFDataset(Dataset):
            def __init__(self, smarts, yields):
                self.smarts = smarts
                self.yields = yields
            def __len__(self): return len(self.smarts)
            def __getitem__(self, idx):
                enc = hf_tok(self.smarts[idx], max_length=MAX_SEQ_LEN,
                              truncation=True, padding="max_length",
                              return_tensors="pt")
                return {
                    "input_ids":      enc["input_ids"].squeeze(0),
                    "attention_mask": enc["attention_mask"].squeeze(0),
                    "yield":          torch.tensor(self.yields[idx], dtype=torch.float32),
                }

        train_ds = HFDataset(s_train, y_train)
        val_ds   = HFDataset(s_val,   y_val)
        test_ds  = HFDataset(s_test,  y_test)
        model    = ChemBERTaYieldModel(dropout=DROPOUT)

    model = model.to(device)
    if distributed:
        model = nn.parallel.DistributedDataParallel(model, device_ids=[local_rank])

    n_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    if is_main:
        print(f"  Model parameters: {n_params:,}")

    # ── DataLoaders ───────────────────────────────────────────────────────
    train_sampler = (torch.utils.data.distributed.DistributedSampler(train_ds)
                     if distributed else None)
    train_loader = DataLoader(train_ds, batch_size=args.batch_size,
                              shuffle=(train_sampler is None),
                              sampler=train_sampler, num_workers=4, pin_memory=True)
    val_loader  = DataLoader(val_ds,  batch_size=args.batch_size * 2,
                             shuffle=False, num_workers=2, pin_memory=True)
    test_loader = DataLoader(test_ds, batch_size=args.batch_size * 2,
                             shuffle=False, num_workers=2, pin_memory=True)

    # ── Optimizer & schedule ──────────────────────────────────────────────
    optimizer  = torch.optim.AdamW(model.parameters(), lr=args.lr,
                                   weight_decay=WEIGHT_DECAY, betas=(0.9, 0.999))
    total_steps = len(train_loader) * args.epochs
    scheduler   = get_cosine_schedule_with_warmup(optimizer, WARMUP_STEPS, total_steps)
    loss_fn     = nn.MSELoss()

    best_val_rmse  = float("inf")
    best_ckpt_path = os.path.join(MODEL_DIR, "rxn_transformer_best.pt")
    history        = []

    # ── Training loop ─────────────────────────────────────────────────────
    if is_main:
        print(f"\nTraining for {args.epochs} epochs...\n")

    for epoch in range(1, args.epochs + 1):
        if distributed:
            train_sampler.set_epoch(epoch)

        model.train()
        epoch_loss = 0.0
        t0 = time.time()

        for batch in (tqdm(train_loader, desc=f"Epoch {epoch}") if is_main else train_loader):
            ids   = batch["input_ids"].to(device)
            mask  = batch["attention_mask"].to(device)
            y     = batch["yield"].to(device)
            preds = model(ids, mask)
            loss  = loss_fn(preds, y)
            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            epoch_loss += loss.item()

        avg_loss = epoch_loss / len(train_loader)
        val_rmse, val_mae, val_r2 = evaluate(model, val_loader, device)
        elapsed = time.time() - t0

        if is_main:
            print(f"Epoch {epoch:03d} | loss {avg_loss:.3f} | "
                  f"val RMSE {val_rmse:.2f}%  MAE {val_mae:.2f}%  R² {val_r2:.4f} | "
                  f"{elapsed:.0f}s")
            history.append({"epoch": epoch, "loss": avg_loss,
                            "val_rmse": val_rmse, "val_mae": val_mae, "val_r2": val_r2})

            if val_rmse < best_val_rmse:
                best_val_rmse = val_rmse
                raw = model.module if distributed else model
                torch.save({
                    "epoch":       epoch,
                    "model_state": raw.state_dict(),
                    "val_rmse":    val_rmse,
                    "val_r2":      val_r2,
                    "backbone":    args.backbone,
                    "vocab_size":  len(vocab) if args.backbone == "scratch" else None,
                }, best_ckpt_path)
                print(f"  ✓ New best checkpoint saved (val RMSE {val_rmse:.2f}%)")

    # ── Final test evaluation ─────────────────────────────────────────────
    if is_main:
        print("\nLoading best checkpoint for final evaluation...")
        ckpt = torch.load(best_ckpt_path, map_location=device)
        raw  = model.module if distributed else model
        raw.load_state_dict(ckpt["model_state"])

        test_rmse, test_mae, test_r2 = evaluate(model, test_loader, device)

        print(f"\n{'='*60}")
        print(f"  FINAL TEST RESULTS  (best val epoch: {ckpt['epoch']})")
        print(f"{'='*60}")
        print(f"  RMSE : {test_rmse:.2f}%")
        print(f"  MAE  : {test_mae:.2f}%")
        print(f"  R²   : {test_r2:.4f}")

        # Save training history
        hist_path = os.path.join(MODEL_DIR, "rxn_transformer_history.json")
        with open(hist_path, "w") as f:
            json.dump(history, f, indent=2)

        # Save final checkpoint with test metrics
        final_path = os.path.join(MODEL_DIR, "rxn_transformer_final.pt")
        torch.save({
            **ckpt,
            "test_rmse": test_rmse,
            "test_mae":  test_mae,
            "test_r2":   test_r2,
        }, final_path)

        print(f"\n  Best checkpoint : {best_ckpt_path}")
        print(f"  Final checkpoint: {final_path}")
        print(f"  Training history: {hist_path}")
        print("  Done.")

    if distributed:
        torch.distributed.destroy_process_group()


if __name__ == "__main__":
    main()
