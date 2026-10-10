#!/usr/bin/env python3
"""Encode served website images from preserved source JPEGs with public Pillow."""
from pathlib import Path
from PIL import Image, features

ROOT = Path(__file__).resolve().parents[1]


def main():
    if not features.check('webp'):
        raise SystemExit('Install Pillow 12.3.0 with WebP support.')
    inputs = (
        ('website-explainer-poster.jpg', 'website/public/media/explainer-poster.webp',
         {'quality': 80, 'method': 6}),
        ('website-social.jpg', 'website/public/assets/social.jpg',
         {'quality': 75, 'optimize': True, 'progressive': True, 'subsampling': 2}),
    )
    for source, destination, options in inputs:
        with Image.open(ROOT / 'docs/design/masters' / source) as image:
            path = ROOT / destination
            image.save(path, **options)
            print(f'{destination}: {path.stat().st_size} bytes')


if __name__ == '__main__':
    main()
