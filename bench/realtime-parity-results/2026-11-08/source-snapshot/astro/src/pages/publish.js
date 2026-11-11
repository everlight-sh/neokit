import {publish} from '../../../hub.mjs';
export const prerender=false;
export const POST=({request})=>publish(request);
