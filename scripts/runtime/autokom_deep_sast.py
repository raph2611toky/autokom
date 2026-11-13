"""Local, bounded advisory inference on source samples, never a release gate."""
import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

MODEL_SHA = 'befaff21716f1d4a6f06298ef97c54116538a6c16483795b62544e3eaab82062'
MODEL_SIZE = 437958648
MAX_CANDIDATES = 24
MAX_FILE_BYTES = 256 * 1024
CODE_EXTENSIONS = frozenset('.py .js .jsx .ts .tsx .java .go .rs .rb .php .c .cpp .cs .kt'.split())
SKIP_DIRS = frozenset(('.git', 'node_modules', 'vendor', 'venv', 'dist', 'build', 'coverage', '__pycache__', 'target'))
MAX_ENTRIES = 1000
SAFE_PART = re.compile(r'^[A-Za-z0-9_.-]{1,100}$')


def model_files(root):
    weights = root / 'model.safetensors'
    for name in ('config.json', 'tokenizer.json', 'tokenizer_config.json', 'model.safetensors'):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Modèle local absent.')
    if weights.stat().st_size != MODEL_SIZE:
        raise ValueError('Poids non matérialisés ou incomplets.')
    with weights.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != MODEL_SHA:
            raise ValueError('Empreinte du modèle invalide.')


def snippets(root, rows):
    root = root.resolve(strict=True)
    for row in rows[:MAX_CANDIDATES]:
        try:
            candidate = Path(row['path'])
            path = (root / candidate).resolve(strict=True)
            if not path.is_relative_to(root) or path.suffix.lower() not in CODE_EXTENSIONS:
                continue
            relative = path.relative_to(root)
            if any(not SAFE_PART.fullmatch(p) or p.startswith('.') for p in relative.parts):
                continue
            if not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > MAX_FILE_BYTES:
                continue
            first, last = row['start']['line'], row['end']['line']
            if type(first) is not int or type(last) is not int or not 1 <= first <= last:
                continue
            with path.open('rb') as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                continue
            lines = raw.decode('utf-8').splitlines()
            if first > len(lines):
                continue
            yield '\n'.join(lines[max(0, first - 5):min(first + 16, last + 4)])[:7000]
        except (OSError, ValueError, KeyError, TypeError):
            continue


def source_samples(root, limit):
    """Select bounded, deterministic snippets without reading artifacts or dotfiles."""
    seen = count = 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        seen += 1
        if seen > MAX_ENTRIES or count >= limit:
            return
        dirs[:] = sorted(name for name in dirs
                         if SAFE_PART.fullmatch(name) and not name.startswith('.') and name not in SKIP_DIRS)
        for name in sorted(files):
            seen += 1
            if seen > MAX_ENTRIES or count >= limit:
                return
            path = Path(folder) / name
            if path.is_symlink() or not SAFE_PART.fullmatch(name) or name.startswith('.') or Path(name).suffix.lower() not in CODE_EXTENSIONS:
                continue
            if name.endswith(('.min.js', '.bundle.js', '.generated.ts')):
                continue
            # Prefer the first meaningful lines of each eligible source file.
            row = {'path': str(path), 'start': {'line': 1}, 'end': {'line': 17}}
            for snippet in snippets(root, [row]):
                if snippet.strip():
                    count += 1
                    yield snippet


def infer(root, model_root, rows):
    root = root.resolve(strict=True)
    samples = [value for value in snippets(root, rows) if value.strip()]
    scope = 'SEMGREP' if samples else 'SOURCE'
    if not samples:
        samples = list(source_samples(root, MAX_CANDIDATES))
    if not samples:
        return {'status': 'NO_CANDIDATE', 'analyzed': 0}
    model_files(model_root)
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    torch.set_num_threads(2)
    tokenizer = AutoTokenizer.from_pretrained(model_root, local_files_only=True, trust_remote_code=False)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_root, local_files_only=True, trust_remote_code=False, use_safetensors=True)
    model.eval()
    counts = {}
    with torch.inference_mode():
        for offset in range(0, len(samples), 4):
            batch = tokenizer(samples[offset:offset + 4], return_tensors='pt',
                              truncation=True, max_length=512, padding=True)
            for prediction in model(**batch).logits.argmax(dim=1).tolist():
                key = str(prediction)
                counts[key] = counts.get(key, 0) + 1
    return {'status': 'COMPLETE', 'analyzed': len(samples), 'classes': counts, 'scope': scope}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('model', type=Path)
    args = parser.parse_args()
    try:
        rows = json.load(sys.stdin)
        print(json.dumps(infer(args.source, args.model, rows)))
    except Exception:
        # No traceback, filenames, code snippets or credentials in Jenkins.
        print(json.dumps({'status': 'UNAVAILABLE', 'analyzed': 0}))


if __name__ == '__main__':
    main()
