import mark from './assets/sbarbase-mark-96.webp';
import cactusPhoto from './assets/cactus-photo-384.webp';
import cactusPhotoLarge from './assets/cactus-photo-768.webp';
import cactusAvif from './assets/cactus-photo-384.avif';
import cactusAvifLarge from './assets/cactus-photo-768.avif';

export function Brand(){
 return <div className="wordmark"><img src={mark} alt="" width="32" height="38"/><span>sbarbase</span></div>;
}

export function CactusArtwork(){
 const sizes='(max-width: 1000px) 280px, 360px';
 return <div className="botanical-art" aria-hidden="true"><picture>
  <source media="(min-width: 761px)" type="image/avif" srcSet={`${cactusAvif} 384w, ${cactusAvifLarge} 768w`} sizes={sizes}/>
  <source media="(min-width: 761px)" type="image/webp" srcSet={`${cactusPhoto} 384w, ${cactusPhotoLarge} 768w`} sizes={sizes}/>
  <img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" width="768" height="1152" alt="" decoding="async" fetchPriority="low"/>
 </picture></div>;
}
