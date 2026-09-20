import mark from './assets/sbarbase-mark-96.webp';
import cactusPhoto from './assets/cactus-photo-384.webp';
import cactusPhotoLarge from './assets/cactus-photo-768.webp';

export function Brand(){
 return <div className="wordmark"><img src={mark} alt="" width="32" height="38"/><span>sbarbase</span></div>;
}

export function CactusArtwork(){
 return <div className="botanical-art" aria-hidden="true"><img src={cactusPhoto} srcSet={`${cactusPhoto} 384w, ${cactusPhotoLarge} 768w`} sizes="(max-width: 760px) 1px, (max-width: 1000px) 280px, 360px" width="768" height="1152" alt="" decoding="async"/></div>;
}
