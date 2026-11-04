"""Image-local multi-marker measurements from saved, reviewable nuclear masks."""
import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import cKDTree
import cv2

from core.measurement_relations import geometry_record, measurement_id
from core.spatial_demo import DemoResult, spatial_metrics


def estimate_marker_thresholds(image, source, *, markers, expansion=3., pixel_size_xy=None, roi_mask=None):
    """Otsu starting points on included cell means, in the same raw units as measurement."""
    from skimage.filters import threshold_otsu
    result = measure_multiplex(image, source, markers=[dict(m, threshold=0.) for m in markers],
                               expansion=expansion, pixel_size_xy=pixel_size_xy, roi_mask=roi_mask)
    values = np.asarray([row['marker_means'] for row in result['object_table'] if row['include_in_statistics']])
    if len(values) < 2:
        return [None for _ in markers]
    return [float(threshold_otsu(plane)) if np.ptp(plane) > 1e-12 else None for plane in values.T]


def measure_multiplex(image, source, *, markers, expansion=3., radius=20.,
                      source_phenotype=None, target_phenotype=None,
                      pixel_size_xy=None, roi_mask=None, niche_clusters=0):
    image = np.asarray(image)
    if image.ndim != 3 or not markers or not np.isfinite([expansion, radius]).all() or expansion < 0 or radius <= 0:
        raise ValueError('Choose marker channels and positive spatial parameters.')
    channels = [int(m['channel_index']) for m in markers]
    names = [str(m['name']).strip() for m in markers]
    if not all(names) or len(set(names)) != len(names):
        raise ValueError('Marker names must be nonempty and distinct.')
    if len(set(channels)) != len(channels) or any(c < 0 or c >= image.shape[0] for c in channels):
        raise ValueError('Marker channels must be distinct channels of this image.')
    cutoffs = np.asarray([m['threshold'] for m in markers], float)
    if not np.isfinite(cutoffs).all():
        raise ValueError('Marker cutoffs must be finite raw intensities.')
    scale = np.asarray(pixel_size_xy if pixel_size_xy is not None else (1., 1.), float)
    if scale.shape != (2,) or not np.isfinite(scale).all() or np.any(scale <= 0):
        raise ValueError('Invalid X/Y calibration.')
    geometry = geometry_record(source, image.shape[1:])
    nuclei = geometry['labels']
    roi = geometry['roi_mask'].copy()
    if roi_mask is not None:
        if np.shape(roi_mask) != nuclei.shape:
            raise ValueError('ROI must match native image dimensions.')
        roi &= roi_mask
    ids = np.unique(nuclei[nuclei > 0])
    if len(ids):
        distance, nearest = ndi.distance_transform_edt(nuclei == 0, sampling=scale[::-1], return_indices=True)
        cells = np.where(distance <= expansion, nuclei[tuple(nearest)], 0)
        means = np.column_stack([ndi.mean(image[c], cells, ids) for c in channels])
        xy_px = np.asarray(ndi.center_of_mass(nuclei > 0, nuclei, ids))[:, ::-1]
        flags = means >= cutoffs
        combinations, codes = np.unique(flags, axis=0, return_inverse=True)
    else:
        cells = nuclei.copy()
        means, xy_px = np.empty((0, len(markers))), np.empty((0, 2))
        combinations, codes = np.empty((0, len(markers)), bool), np.empty(0, int)
    titles = [' / '.join(str(m['name'])+('+' if on else '−') for m, on in zip(markers, combination))
              for combination in combinations]
    selected = np.zeros(len(ids), bool)
    if len(ids):
        centers = np.rint(xy_px).astype(int)
        centers[:, 0] = np.clip(centers[:, 0], 0, nuclei.shape[1]-1)
        centers[:, 1] = np.clip(centers[:, 1], 0, nuclei.shape[0]-1)
        selected = roi[centers[:, 1], centers[:, 0]]
    objects_by_id = {r['label_id']: r for r in geometry['objects']}
    excluded = set(geometry['overlapping_ids'])
    for i, identifier in enumerate(ids):
        selected[i] &= objects_by_id[int(identifier)].get('include_in_statistics', True) and identifier not in excluded
    area = float(roi.sum()*np.prod(scale))
    if area <= 0:
        raise ValueError('ROI has no measurable area.')
    r = DemoResult(nuclei, cells, ids, xy_px*scale, means, codes, area, selected, (0, 0, nuclei.shape[1], nuclei.shape[0]))
    present = sorted(set(codes[selected].tolist()))
    choose = lambda title, default: titles.index(title) if title in titles else default if title is None else -1
    a = choose(source_phenotype, present[0] if present else -1)
    b = choose(target_phenotype, present[-1] if present else -1)
    metrics = spatial_metrics(r, a, b, radius_um=radius)
    neighbors = {int(i): (float(d) if np.isfinite(d) else None, int(n))
                 for i, d, n in zip(metrics['source_ids'], metrics['nearest'], metrics['neighbors'])}
    included = np.flatnonzero(selected)
    composition = [{} for _ in ids]
    nearest_ids = {}
    targets = np.flatnonzero(selected & (codes == b))
    sources = np.flatnonzero(selected & (codes == a))
    if len(targets) and len(sources):
        _, indices = cKDTree(r.xy[targets]).query(r.xy[sources], k=min(2 if a == b else 1, len(targets)))
        indices = np.asarray(indices).reshape(len(sources), -1)
        for i, candidates in zip(sources, indices):
            candidate = next((targets[j] for j in candidates if targets[j] != i), None)
            if candidate is not None: nearest_ids[int(ids[i])] = int(ids[candidate])
    if len(included):
        tree = cKDTree(r.xy[included])
        for i in included:
            adjacent = included[np.asarray(tree.query_ball_point(r.xy[i], radius), int)]
            adjacent = adjacent[adjacent != i]
            values, counts = np.unique(codes[adjacent], return_counts=True)
            composition[i] = {titles[c]: int(n) for c, n in zip(values, counts)}
    niche_labels = np.zeros(len(ids), int)
    niche_summary = []
    if niche_clusters and len(included):
        from scipy.sparse import csr_matrix
        from sklearn.cluster import KMeans
        indices, columns, values = [], [], []
        for row_index, i in enumerate(included):
            total_neighbors = sum(composition[i].values())
            for title, count in composition[i].items():
                indices.append(row_index); columns.append(titles.index(title)); values.append(count/max(1, total_neighbors))
        features = csr_matrix((values, (indices, columns)), shape=(len(included), len(titles)))
        clusters = min(max(1, int(niche_clusters)), len(included))
        import warnings
        from sklearn.exceptions import ConvergenceWarning
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', ConvergenceWarning)
            groups = KMeans(n_clusters=clusters, random_state=773, n_init=10).fit_predict(features)
        # Stable ordering by the cluster's average phenotype composition.
        ordered = sorted(np.unique(groups), key=lambda g: tuple(np.asarray(features[groups == g].mean(axis=0)).ravel()))
        for number, group in enumerate(ordered, 1):
            positions = included[groups == group]; niche_labels[positions] = number
            profile = np.asarray(features[groups == group].mean(axis=0)).ravel()
            niche_summary.append(dict(niche=number, cells=int(len(positions)),
                                      mean_neighbor_composition={title: float(value) for title, value in zip(titles, profile) if value > 0}))
    cell_areas = np.bincount(cells.ravel())
    slices = ndi.find_objects(cells)
    colors = ('#A0A0A0', '#3ED995', '#F5A65B', '#BE91EA', '#40C7DF', '#EE7B96', '#DCCF61', '#89A7F7')
    from colorsys import hsv_to_rgb
    phenotype_colors=[]
    for combination in combinations:
        color_index=sum(1 << j for j,on in enumerate(combination) if on)
        rgb=hsv_to_rgb((color_index*.61803398875)%1.,.55,.95)
        phenotype_colors.append(colors[color_index] if color_index<len(colors) else '#'+''.join(f'{round(v*255):02X}' for v in rgb))
    rows = []
    for i, identifier in enumerate(ids):
        original = objects_by_id[int(identifier)]
        d, n = neighbors.get(int(identifier), (None, None))
        contour = []
        region = slices[int(identifier)-1]
        if region is not None:
            yy, xx = region
            paths, _ = cv2.findContours(np.uint8(cells[region] == identifier), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if paths:
                contour = (max(paths, key=cv2.contourArea)[:, 0]+[xx.start, yy.start]).tolist()
        rows.append(dict(object_id=int(identifier), source_object_id=original['object_id'],
                         center_x_px=float(xy_px[i, 0]), center_y_px=float(xy_px[i, 1]),
                         x=float(r.xy[i, 0]), y=float(r.xy[i, 1]), phenotype=titles[codes[i]],
                         marker_means=[float(v) for v in means[i]],
                         nearest_target=d, target_count_in_radius=n,
                         nearest_target_id=nearest_ids.get(int(identifier)), neighbor_composition=composition[i],
                         estimated_cell_area=float(cell_areas[identifier]*np.prod(scale)),
                         cell_contour_points_px=contour, niche=int(niche_labels[i]),
                         include_in_statistics=bool(selected[i]),
                         contour_points_px=original.get('contour_points_px', original.get('contour_px', [])),
                         overlay_color=phenotype_colors[int(codes[i])]))
    finite = metrics['nearest'][np.isfinite(metrics['nearest'])]
    total = int(selected.sum())
    summary = dict(cells=total, excluded=len(ids)-total, area=area,
                   area_unit='um2' if pixel_size_xy is not None else 'px2',
                   length_unit='um' if pixel_size_xy is not None else 'px',
                   density=total/area, source_cells=metrics['source_n'], target_cells=metrics['target_n'],
                   nearest_mean=float(finite.mean()) if len(finite) else None,
                   nearest_median=float(np.median(finite)) if len(finite) else None,
                   fraction_with_neighbors=float(np.mean(metrics['neighbors'] > 0)) if len(metrics['neighbors']) else None,
                   mean_target_neighbors=float(metrics['neighbors'].mean()) if len(metrics['neighbors']) else None,
                   permutation_p=metrics['p'], null_mean=metrics['null_mean'])
    phenotypes = [dict(phenotype=titles[c], cells=int(np.sum(selected & (codes == c))),
                       fraction=float(np.sum(selected & (codes == c))/total),
                       density=float(np.sum(selected & (codes == c))/area)) for c in present] if total else []
    return dict(type='multiplex_spatial', image_uid=source['image_uid'], image_name=source.get('image_name', ''),
                channel_index=source.get('channel_index'), object_table=rows, summary=summary, phenotypes=phenotypes,
                source_measurements={'nuclei': measurement_id(source)},
                parameters=dict(markers=markers, expansion=expansion, radius=radius, niche_clusters=int(niche_clusters),
                                source_phenotype=source_phenotype if source_phenotype is not None else titles[a] if a >= 0 else None,
                                target_phenotype=target_phenotype if target_phenotype is not None else titles[b] if b >= 0 else None, pixel_size_xy=pixel_size_xy),
                niches=niche_summary, niche_method='KMeans of radius-neighbor phenotype fractions; exploratory, not biological identity',
                nearest_distances=[float(d) for d in finite],
                measurement_source='raw', scope='centroid_in_roi; neighbors_in_roi; self_excluded',
                spatial_test='199 conditional phenotype-label permutations; no edge correction')


def grouped_spatial_results(results):
    """Descriptive per-ROI comparisons, never pool cells as independent replicates."""
    latest = {}
    for result in results:
        p = result.get('requested_parameters', {})
        scope = result.get('roi_snapshot')
        import json
        settings = {k: result['parameters'].get(k) for k in ('markers', 'expansion', 'radius', 'source_phenotype', 'target_phenotype')}
        signature = json.dumps(settings, sort_keys=True, ensure_ascii=False)
        key = (result['image_uid'], json.dumps(scope, sort_keys=True), p.get('roi_type', 'All'), signature)
        latest[key] = result
    groups = {}
    for result in latest.values():
        p = result.get('requested_parameters', {})
        settings = {k: result['parameters'].get(k) for k in ('markers', 'expansion', 'radius', 'source_phenotype', 'target_phenotype')}
        key = (p.get('experiment_group', 'Ungrouped'), p.get('roi_type', 'All'), result['summary']['length_unit'], json.dumps(settings, sort_keys=True, ensure_ascii=False))
        groups.setdefault(key, []).append(result)
    rows = []
    for (group, region, unit, settings), observations in groups.items():
        def mean_metric(name):
            values = [r['summary'].get(name) for r in observations if r['summary'].get(name) is not None]
            return float(np.mean(values)) if values else None
        for phenotype in sorted({p['phenotype'] for r in observations for p in r['phenotypes']}):
            counts, fractions, densities = [], [], []
            for result in observations:
                item = next((p for p in result['phenotypes'] if p['phenotype'] == phenotype), {})
                counts.append(item.get('cells', 0)); fractions.append(item.get('fraction', 0.)); densities.append(item.get('density', 0.))
            rows.append(dict(experiment_group=group, roi_type=region, length_unit=unit, phenotype=phenotype,
                             images=len({r['image_uid'] for r in observations}), roi_observations=len(observations),
                             mean_count=float(np.mean(counts)), mean_fraction=float(np.mean(fractions)),
                             mean_density=float(np.mean(densities)),
                             mean_roi_area=mean_metric('area'), mean_total_cells=mean_metric('cells'),
                             mean_nearest_distance=mean_metric('nearest_mean'),
                             mean_roi_nearest_median=mean_metric('nearest_median'),
                             mean_neighbor_count=mean_metric('mean_target_neighbors'),
                             mean_fraction_with_neighbors=mean_metric('fraction_with_neighbors'),
                             analysis_settings=json.loads(settings),
                             scope='Latest per-image/ROI; descriptive ROI means; no independence assumed for overlapping ROIs'))
    return rows
