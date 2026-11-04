"""Small development comparison on the annotated GNB tissue fields in S-BIAD634.

Data must already be downloaded. All inference runs use native image dimensions.
The field split is fixed before scoring; these are NOT independent patients.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import tifffile


CONFIGS = [
    dict(id='cyto3-d25-raw', model='cyto3', diameter=25, tile=0),
    dict(id='cyto3-d25-tile128', model='cyto3', diameter=25, tile=128),
    dict(id='cyto3-d40-tile128', model='cyto3', diameter=40, tile=128),
    dict(id='nuclei-d25-raw', model='nuclei', diameter=25, tile=0),
]
DEVELOPMENT = [0, 1, 6]
EVALUATION = [2, 3, 4, 7, 8, 9, 10]


def relabel(labels, maximum=2000):
    values, counts = np.unique(labels, return_counts=True)
    keep = values[(values != 0) & (counts >= 8) & (counts <= maximum)]
    lookup = np.zeros(int(labels.max())+1, np.int32)
    lookup[keep] = np.arange(1, len(keep)+1)
    return lookup[labels]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cpu')
    args = parser.parse_args()
    root = args.root.resolve()
    source = root/'.local-runtime/spatial-validation/sources/tissue-annotated/dataset'
    out = root/'.local-runtime/spatial-validation/results/tissue-tuning'
    out.mkdir(parents=True, exist_ok=True)
    plan = dict(dataset='S-BIAD634', development=DEVELOPMENT, evaluation=EVALUATION,
                configs=CONFIGS, maximum_areas=[2000, 5000], minimum_area=8,
                selection='highest development macro instance F1 at IoU>=0.5',
                caveat='Different fields from one cryosection, not patient-held-out validation; pretrained-data overlap unknown')
    plan_path = out/'plan.json'
    if plan_path.exists() and json.loads(plan_path.read_text(encoding='utf-8')) != plan:
        raise ValueError('The existing experiment plan differs; do not overwrite it.')
    plan_path.write_text(json.dumps(plan, indent=2), encoding='utf-8')
    spec = importlib.util.spec_from_file_location('spatial_validation', root/'packaging/validate_spatial_demo.py')
    validation = importlib.util.module_from_spec(spec); spec.loader.exec_module(validation)
    worker = root/'.local-runtime/ScieFig/cellpose/ScieFig_Cellpose.exe'

    def run(index, config):
        name = f'Ganglioneuroblastoma_{index}'
        image = tifffile.imread(source/'rawimages'/(name+'.tif'))
        if image.ndim == 3:
            if image.shape[2] != 3 or not np.array_equal(image[..., 0], image[..., 1]) or not np.array_equal(image[..., 1], image[..., 2]):
                raise ValueError('Unexpected non-identical RGB channels; cannot treat them as one DAPI plane.')
            image = image[..., 0]
        truth = tifffile.imread(source/'groundtruth'/(name+'.tif'))
        if image.shape != truth.shape or truth.dtype.kind not in 'iu':
            raise ValueError('Ground truth must be an integer label image at native dimensions.')
        # Source labels can skip IDs. Remap without altering any pixels.
        identities = np.unique(truth); identities = identities[identities != 0]
        lookup = np.zeros(int(truth.max())+1, np.int32); lookup[identities] = np.arange(1, len(identities)+1)
        truth = lookup[truth]
        cache = out/(name+'-'+config['id']+'.npy')
        if cache.exists():
            labels = np.load(cache, allow_pickle=False)
            metadata = json.loads(cache.with_suffix('.json').read_text(encoding='utf-8'))
        else:
            input_path = out/'inference-input.npy'
            np.save(input_path, image, allow_pickle=False)
            command = [str(worker), str(input_path), str(cache), '--device', args.device,
                       '--model', config['model'], '--diameter', str(config['diameter']), '--min-size', '8']
            if config['tile']:
                command += ['--inference-options', json.dumps(dict(normalization_tile_px=config['tile']))]
            start = time.perf_counter()
            completed = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                       timeout=600, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if completed.returncode:
                raise RuntimeError((completed.stderr or completed.stdout)[-2500:])
            metadata = json.loads(cache.with_suffix('.json').read_text(encoding='utf-8'))
            metadata['elapsed_seconds'] = round(time.perf_counter()-start, 3)
            cache.with_suffix('.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
            labels = np.load(cache, allow_pickle=False)
        assert labels.shape == image.shape
        if metadata.get('model') != config['model'] or metadata.get('diameter_px') != config['diameter']:
            raise ValueError('Model metadata differs from the requested experiment.')
        rows = []
        for maximum in (2000, 5000):
            prediction = relabel(labels, maximum)
            row = dict(sample=name, width=image.shape[1], height=image.shape[0], config=config['id'], maximum_area=maximum,
                       **validation.segmentation_scores(prediction, truth))
            rows.append(row)
        print(name, config['id'], 'F1', round(rows[0]['f1'], 4), round(rows[1]['f1'], 4), flush=True)
        return rows

    development = []
    for config in CONFIGS:
        for index in DEVELOPMENT:
            development += run(index, config)
            (out/'development.json').write_text(json.dumps(development, indent=2), encoding='utf-8')
    choices = [(np.mean([r['f1'] for r in development if r['config'] == c['id'] and r['maximum_area'] == maximum]), c, maximum)
               for c in CONFIGS for maximum in (2000, 5000)]
    score, best, maximum = max(choices, key=lambda entry: entry[0])
    selected = dict(config=best, maximum_area=maximum, development_macro_f1=float(score), frozen_before_evaluation=True)
    (out/'selected.json').write_text(json.dumps(selected, indent=2), encoding='utf-8')
    print('SELECTED', selected, flush=True)
    evaluation = []
    for index in EVALUATION:
        for config in {CONFIGS[0]['id']: CONFIGS[0], best['id']: best}.values():
            evaluation += run(index, config)
            (out/'evaluation.json').write_text(json.dumps(evaluation, indent=2), encoding='utf-8')
    comparisons = []
    for title, config, cutoff in [('previous_reference', CONFIGS[0], 2000), ('selected_reference', best, maximum)]:
        rows = [r for r in evaluation if r['config'] == config['id'] and r['maximum_area'] == cutoff]
        tp, fp, fn = (sum(r[k] for r in rows) for k in ('tp', 'fp', 'fn'))
        comparisons.append(dict(name=title, config=config, maximum_area=cutoff, images=len(rows), tp=tp, fp=fp, fn=fn,
                                micro_f1=2*tp/(2*tp+fp+fn), macro_f1=float(np.mean([r['f1'] for r in rows])),
                                mean_dice=float(np.mean([r['binary_dice'] for r in rows])),
                                worst_f1=min(r['f1'] for r in rows)))
    (out/'summary.json').write_text(json.dumps(comparisons, indent=2), encoding='utf-8')
    print(json.dumps(comparisons, indent=2), flush=True)


if __name__ == '__main__':
    main()
