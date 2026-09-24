// Lays the recorded narration (voice/<lang>/NN.mp3) on the rendered film and writes the site
// media: one MP4 per language with its voice, and matching WebVTT captions timed to the voice.
// Usage: bun build-voice.ts [out/sbarbase-explainer.mp4]
import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';

type Cue = { at: number; file: string; ar: string; en: string };
const here = import.meta.dir;
const spec: { total: number; cues: Cue[] } = await Bun.file(path.join(here, 'narration.json')).json();
const film = path.resolve(process.argv[2] ?? path.join(here, 'out', 'sbarbase-explainer.mp4'));
const media = path.join(here, '..', 'website', 'public', 'media');
const tmp = path.join(here, 'out', 'voice'); mkdirSync(tmp, { recursive: true });
const run = (cmd: string, args: string[]) => execFileSync(cmd, args, { encoding: 'utf8' }).trim();
const seconds = (f: string) => Number(run('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f]));
const stamp = (t: number) => { const m = Math.floor(t / 60), s = t - m * 60; return `${String(m).padStart(2, '0')}:${s.toFixed(3).padStart(6, '0')}`; };

const filmLength = seconds(film);
if (Math.abs(filmLength - spec.total) > 0.1) throw new Error(`film is ${filmLength}s, narration.json expects ${spec.total}s`);

for (const lang of ['ar', 'en'] as const) {
  const clips = spec.cues.map(c => ({ ...c, src: path.join(here, 'voice', lang, `${c.file}.mp3`) }));
  clips.forEach((c, k) => { const end = c.at + seconds(c.src), next = clips[k + 1]?.at ?? spec.total;
    if (end > next) throw new Error(`${lang} ${c.file} ends at ${end.toFixed(2)}s, after ${next}s`); });
  // one voice track: each clip delayed to its cue, mixed, levelled, padded to the film length
  const inputs = clips.flatMap(c => ['-i', c.src]);
  const delays = clips.map((c, k) => `[${k}:a]adelay=${Math.round(c.at * 1000)}:all=1[a${k}]`).join(';');
  const mix = `${delays};${clips.map((_, k) => `[a${k}]`).join('')}amix=inputs=${clips.length}:normalize=0,loudnorm=I=-16:TP=-1.5,apad,atrim=0:${spec.total}[v]`;
  const voice = path.join(tmp, `${lang}.m4a`);
  run('ffmpeg', ['-v', 'error', '-y', ...inputs, '-filter_complex', mix, '-map', '[v]', '-c:a', 'aac', '-b:a', '96k', '-ac', '1', voice]);
  run('ffmpeg', ['-v', 'error', '-y', '-i', film, '-i', voice, '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-crf', '22',
    '-preset', 'slow', '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-movflags', '+faststart', '-shortest', path.join(media, `explainer.${lang}.mp4`)]);
  const vtt = ['WEBVTT', '', ...clips.flatMap(c => [`${stamp(c.at)} --> ${stamp(c.at + seconds(c.src) + 0.2)}`, c[lang], ''])].join('\n');
  writeFileSync(path.join(media, `explainer.${lang}.vtt`), vtt);
  console.log(`explainer.${lang}.mp4 and .vtt written`);
}
