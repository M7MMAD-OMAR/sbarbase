import {createRoot} from 'react-dom/client';
import {App} from './App';
import './style.css';
import {applyTheme,storedTheme} from './theme';
// Applied before the first render so an explicit override does not flash the
// system theme. The page itself declares no inline script.
applyTheme(storedTheme());
const root=document.getElementById('root');if(!root)throw new Error('Missing app root');createRoot(root).render(<App/>);
