#!/usr/bin/env python3
"""Rebuild transparent console artwork using the public Pillow encoder."""
from pathlib import Path
from PIL import Image, features

ROOT = Path(__file__).resolve().parents[1]


def main():
    if not features.check('avif') or not features.check('webp'):
        raise SystemExit('Install Pillow 12.3.0 with AVIF and WebP support.')
    with Image.open(ROOT / 'docs/design/masters/cactus-photo.png') as original:
        for width in (384, 768):
            image = original.resize((width, width * 3 // 2), Image.Resampling.LANCZOS)
            target = ROOT / f'ui/assets/cactus-photo-{width}'
            for extension, options in (
                ('webp', {'quality': 80, 'method': 6}),
                ('avif', {'quality': 60, 'speed': 6, 'subsampling': '4:4:4'}),
            ):
                path = target.with_suffix('.' + extension)
                image.save(path, **options)
                print(f'{path.relative_to(ROOT)}: {path.stat().st_size} bytes')


if __name__ == '__main__':
    main()
