import { start } from '../../../client/runtime.js';
start({ widgets: { 'stream-counter': () => import('./stream-counter.js') } });
