import {mount} from '../../../client/performance.js';
const badge=document.querySelector('[data-performance]');if(badge)mount(badge);
const progress=document.querySelector('.reading-progress');if(progress&&document.querySelector('.article')){const update=()=>{const total=document.documentElement.scrollHeight-innerHeight;progress.value=total>0?Math.min(1,Math.max(0,scrollY/total)):0;};addEventListener('scroll',update,{passive:true});addEventListener('resize',update);update();}
const category=new URL(location.href).searchParams.get('category')||'all';for(const link of document.querySelectorAll('.categories a'))if((new URL(link.href).searchParams.get('category')||'all')===category)link.setAttribute('aria-current','page');
