"""Private offline inference worker with explicit CPU, CUDA and MPS selection."""
import os
import sys
from pathlib import Path
import argparse
import json
from importlib.metadata import version

os.environ.setdefault('OMP_NUM_THREADS', '4')
os.environ.setdefault('MKL_NUM_THREADS', '4')


def select_device(torch, requested):
    available = {'cpu': True, 'cuda': torch.cuda.is_available(),
                 'mps': torch.backends.mps.is_available()}
    if requested == 'auto':
        requested = next(name for name in ('cuda', 'mps', 'cpu') if available[name])
    if not available.get(requested, False):
        name = 'Apple GPU (MPS)' if requested == 'mps' else 'NVIDIA GPU (CUDA)'
        raise RuntimeError(f'{name} is unavailable. Select Model: CPU in the application toolbar.')
    return torch.device(requested)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('target')
    parser.add_argument('--model',choices=['cyto3','nuclei','cyto3_dapi_reviewed_v1','cyto3_he_fiber_boundary_v1','cyto3_laminin_final_v1','truefad_myotube'],default='cyto3')
    parser.add_argument('--diameter',type=float,default=30.)
    parser.add_argument('--min-size',type=int,default=60)
    parser.add_argument('--inference-options',default='{}')
    parser.add_argument('--device', choices=['auto', 'cpu', 'mps', 'cuda'], default='auto')
    args=parser.parse_args()
    import numpy as np
    import torch
    from cellpose import models
    torch.set_num_threads(4)
    device = select_device(torch, args.device)
    if args.model=='truefad_myotube':
        from truefad_model import predict_centers
        root=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
        weights=root/'models'/'truefad_myotube.pt'
        if not weights.is_file():raise FileNotFoundError('Bundled TRUEFAD weights are missing; update the myotube runtime.')
        output=predict_centers(np.load(args.source,allow_pickle=False),weights,device=device)
        np.save(args.target,output,allow_pickle=False)
        Path(args.target).with_suffix('.json').write_text(json.dumps(dict(model=args.model,
            model_weights=weights.name,model_origin='TRUEFAD original weights; PyTorch transcription without training',
            output='native_center_response',normalization_percentiles=[1,99.8],tile_size=512,
            overlap_blend='positive_edge_distance',device=str(device),torch_version=version('torch'))),encoding='utf-8')
        return
    options = dict(normalization_tile_px=0, invert=False, niter=200, flow_threshold=.4, cellprob_threshold=0.)
    requested = json.loads(args.inference_options)
    if not isinstance(requested, dict) or set(requested)-set(options):
        raise ValueError('Unknown Cellpose inference option.')
    options.update(requested)
    if (not np.isfinite([options[k] for k in ('normalization_tile_px','niter','flow_threshold','cellprob_threshold')]).all()
            or int(options['normalization_tile_px']) != options['normalization_tile_px'] or options['normalization_tile_px'] < 0
            or int(options['niter']) != options['niter'] or options['niter'] < 0 or options['flow_threshold'] < 0
            or not isinstance(options['invert'], bool)):
        raise ValueError('Invalid Cellpose inference options.')
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    weights = root / 'models' / ('nucleitorch_0' if args.model=='nuclei' else args.model)
    if not weights.is_file():
        raise FileNotFoundError('Bundled '+args.model+' weights are missing; reinstall ScieFig.')
    image = np.load(args.source, allow_pickle=False)
    model = models.CellposeModel(device=device, pretrained_model=str(weights))
    normalize = dict(normalize=True, percentile=[1.,99.], invert=options['invert'],
                     tile_norm_blocksize=options['normalization_tile_px'])
    labels, _, _ = model.eval(image, diameter=args.diameter, channels=[0,0], normalize=normalize,
                              flow_threshold=options['flow_threshold'], cellprob_threshold=options['cellprob_threshold'],
                              min_size=args.min_size, batch_size=8, niter=options['niter'])
    np.save(args.target, labels.astype(np.int32), allow_pickle=False)
    Path(args.target).with_suffix('.json').write_text(json.dumps(dict(model=args.model,model_weights=weights.name,diameter_px=args.diameter,
        min_size_px=args.min_size,flow_threshold=options['flow_threshold'],cellprob_threshold=options['cellprob_threshold'],normalize=normalize,
        channels=[0,0],niter=options['niter'],inference_options=options,device=str(model.device),cellpose_version=version('cellpose'))),encoding='utf-8')


if __name__ == '__main__':
    main()
