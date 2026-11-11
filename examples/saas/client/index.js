import {mount as performanceBadge} from '../../../client/performance.js';
import {mount as chart} from './chart.js';
const badge=document.querySelector('[data-performance]');if(badge)performanceBadge(badge);
for(const root of document.querySelectorAll('[data-chart]'))chart(root);
for(const link of document.querySelectorAll('[data-nav]'))if(link.dataset.nav===location.pathname)link.setAttribute('aria-current','page');

import {mount as live} from './live.js';
const liveRoot=document.querySelector('[data-live]');if(liveRoot)live(liveRoot);
