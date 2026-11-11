"""Build-time metadata, responsive images and precompressed public assets."""
import gzip
import hashlib
import html
import io
from pathlib import Path
from .templates import BuildError


def metadata(config, page):
    site=config.get('site',{})
    values={**site,**page.get('metadata',{})}
    parts=[]
    for name in ('description','robots'):
        if name in values:
            if not isinstance(values[name],str):raise BuildError(f'Metadata {name} must be text')
            parts.append(f'<meta name="{name}" content="{html.escape(values[name],quote=True)}">')
    for name in ('title','description','image'):
        key='og_'+name
        if key in values:parts.append(f'<meta property="og:{name}" content="{html.escape(str(values[key]),quote=True)}">')
    canonical=values.get('canonical')
    if canonical:
        if not isinstance(canonical,str) or not canonical.startswith(('https://','http://')):raise BuildError('Canonical metadata requires an HTTP URL')
        parts.append(f'<link rel="canonical" href="{html.escape(canonical,quote=True)}">')
    return ''.join(parts)


def generated_assets(app,config,routes):
    files={};bindings={}
    origin=config.get('site',{}).get('url','').rstrip('/')
    if origin:
        if not origin.startswith(('https://','http://')) or any(c in origin for c in '\r\n<>"'):
            raise BuildError('[site] url must be an HTTP origin')
        paths=sorted({p for route in routes if route['config'].get('export',True) for p in route['paths']})
        entries=''.join('<url><loc>'+html.escape(origin+path)+'</loc></url>' for path in paths)
        files['/sitemap.xml']=('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+entries+'</urlset>').encode()
        files['/robots.txt']=('User-agent: *\nAllow: /\nSitemap: '+origin+'/sitemap.xml\n').encode()
    images=config.get('images',{})
    sources=images.get('files',[])
    if sources:
        try:from PIL import Image,ImageOps
        except ImportError as error:raise BuildError('Responsive images require Pillow in the build environment') from error
        widths=images.get('widths',[320,640,1280])
        formats=images.get('formats',['webp'])
        if not isinstance(widths,list) or any(type(w)!=int or w<1 or w>8192 for w in widths):raise BuildError('Image widths must be integers in 1..8192')
        if not formats or any(f not in ('webp','jpeg','png','avif') for f in formats):raise BuildError('Supported image formats: webp, jpeg, png, avif')
        for source in sources:
            path=(app/'public'/source).resolve()
            if not path.is_relative_to((app/'public').resolve()) or not path.is_file():raise BuildError(f'Image source must be a public file: {source}')
            with Image.open(path) as raw:
                if raw.width*raw.height>40000000:raise BuildError('Image exceeds 40 megapixels')
                image=ImageOps.exif_transpose(raw)
                for width in sorted(set(min(w,image.width) for w in widths)):
                    resized=image.resize((width,max(1,round(image.height*width/image.width))))
                    for format_ in formats:
                        prepared=resized.convert('RGB') if format_=='jpeg' else resized
                        buffer=io.BytesIO();prepared.save(buffer,format=format_.upper(),quality=82)
                        content=buffer.getvalue();digest=hashlib.sha256(content).hexdigest()[:12]
                        url=f'/_web/images/{path.stem}-{width}-{digest}.{format_}'
                        files[url]=content;bindings[f'image:{source}:{width}:{format_}']=url
    return files,bindings


def compressed(data):
    result=gzip.compress(data,compresslevel=9,mtime=0)
    return result if len(data)>=512 and len(result)<len(data) else None
