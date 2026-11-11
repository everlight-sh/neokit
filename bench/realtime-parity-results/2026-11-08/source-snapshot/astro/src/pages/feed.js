import {feed} from '../../../hub.mjs';
export const prerender=false;
export const GET=({request})=>feed(request);
