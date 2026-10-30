import apple from './assets/providers/apple.svg';
import bitbucket from './assets/providers/bitbucket.svg';
import discord from './assets/providers/discord.svg';
import facebook from './assets/providers/facebook.svg';
import figma from './assets/providers/figma.svg';
import github from './assets/providers/github.svg';
import gitlab from './assets/providers/gitlab.svg';
import google from './assets/providers/google.svg';
import kakao from './assets/providers/kakao.svg';
import keycloak from './assets/providers/keycloak.svg';
import linkedin_oidc from './assets/providers/linkedin_oidc.svg';
import notion from './assets/providers/notion.svg';
import slack_oidc from './assets/providers/slack_oidc.svg';
import spotify from './assets/providers/spotify.svg';
import twitch from './assets/providers/twitch.svg';
import twitter from './assets/providers/twitter.svg';
import zoom from './assets/providers/zoom.svg';

/** Local brand marks (ui/assets/providers/SOURCES.md). A provider without a file shows a monogram tile. */
const ICONS:Record<string,string>={apple,bitbucket,discord,facebook,figma,github,gitlab,google,kakao,keycloak,linkedin_oidc,notion,slack_oidc,spotify,twitch,twitter,zoom};

export function ProviderIcon({name,label}:{name:string;label:string}){
 const source=ICONS[name];
 return <span className="aw-icon" aria-hidden="true">{source?<img src={source} alt="" width={20} height={20}/>:<span className="aw-monogram">{label.slice(0,1).toUpperCase()}</span>}</span>;
}
