"""Build-time metadata, responsive images and precompressed public assets."""
import gzip
import hashlib
import html
import io
import os
from pathlib import Path
from .templates import BuildError, literal

try:
    import brotli as _brotli
except ImportError:  # Brotli variants are optional; gzip is always produced.
    _brotli = None

# Page props that can supply metadata per request: field -> required prop kind.
DYNAMIC_METADATA = {'description':'text', 'robots':'text', 'og_title':'text', 'og_description':'text',
                    'og_image':'url', 'canonical':'url', 'jsonld':'text'}
COMPRESSIBLE = ('text/', 'application/javascript', 'application/json', 'application/xml',
                'application/manifest+json', 'application/wasm', 'image/svg')


def metadata(config, page, skip=()):
    site=config.get('site',{})
    values={**site,**{key:value for key,value in page.get('metadata',{}).items() if key!='props'}}
    parts=[]
    for name in ('description','robots'):
        if name in values and name not in skip:
            if not isinstance(values[name],str):raise BuildError(f'Metadata {name} must be text')
            parts.append(f'<meta name="{name}" content="{html.escape(values[name],quote=True)}">')
    for name in ('title','description','image'):
        key='og_'+name
        if key in values and key not in skip:parts.append(f'<meta property="og:{name}" content="{html.escape(str(values[key]),quote=True)}">')
    canonical=values.get('canonical')
    if canonical and 'canonical' not in skip:
        if not isinstance(canonical,str) or not canonical.startswith(('https://','http://')):raise BuildError('Canonical metadata requires an HTTP URL')
        parts.append(f'<link rel="canonical" href="{html.escape(canonical,quote=True)}">')
    return ''.join(parts)


def metadata_props(page):
    mapping=page.get('metadata',{}).get('props',{})
    if not isinstance(mapping,dict):raise BuildError('[metadata.props] must be a table of metadata field = prop name')
    return mapping


def dynamic_metadata(config, page, props):
    """Neo expressions for metadata taken from the loader's props, escaped per request."""
    origin=config.get('site',{}).get('url','').rstrip('/')
    parts=[]
    jsonld=False
    for key,prop in metadata_props(page).items():
        if key not in DYNAMIC_METADATA:
            raise BuildError('[metadata.props] supports '+', '.join(DYNAMIC_METADATA))
        kind=DYNAMIC_METADATA[key]
        if not isinstance(prop,str) or props.get(prop)!=kind:
            raise BuildError(f'[metadata.props] {key} must name a {kind} prop of the page template')
        value='props.'+prop
        if key in ('description','robots'):
            parts+=[literal(f'<meta name="{key}" content="'),f'html_escape({value})',literal('">')]
        elif key in ('og_title','og_description'):
            parts+=[literal(f'<meta property="og:{key[3:]}" content="'),f'html_escape({value})',literal('">')]
        elif key=='jsonld':
            jsonld=True
            parts+=[literal('<script type="application/ld+json">'),f'neokit_json_script({value})',literal('</script>')]
        else:
            if not origin:raise BuildError(f'[metadata.props] {key} needs an absolute [site] url')
            opening=f'<meta property="og:image" content="{html.escape(origin,quote=True)}' if key=='og_image' else f'<link rel="canonical" href="{html.escape(origin,quote=True)}'
            parts+=[literal(opening),f'html_escape({value}.value)',literal('">')]
    return parts,jsonld


def image_variants(path,widths,formats,cache):
    """Encoded responsive variants keyed by (actual width, format); cached by content."""
    from PIL import Image,ImageOps,__version__ as pillow
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    def cached(width,format_):
        return None if cache is None else cache/f'{hashlib.sha256(f"{digest}:{width}:{format_}:82:{pillow}".encode()).hexdigest()}.{format_}'
    variants={}
    with Image.open(path) as raw:
        if raw.width*raw.height>40000000:raise BuildError('Image exceeds 40 megapixels')
        # EXIF orientations 5-8 swap the displayed width and height.
        original=raw.height if raw.getexif().get(0x0112,1) in (5,6,7,8) else raw.width
        image=None
        for width in sorted({min(w,original) for w in widths}):
            missing=[f for f in formats if not (cached(width,f) and cached(width,f).is_file())]
            if missing:
                if image is None:image=ImageOps.exif_transpose(raw)
                resized=image.resize((width,max(1,round(image.height*width/image.width))),Image.Resampling.LANCZOS)
            for format_ in formats:
                target=cached(width,format_)
                if format_ in missing:
                    prepared=resized.convert('RGB') if format_=='jpeg' else resized
                    buffer=io.BytesIO();prepared.save(buffer,format=format_.upper(),quality=82)
                    content=buffer.getvalue()
                    if target is not None:
                        target.parent.mkdir(parents=True,exist_ok=True)
                        staged=target.with_suffix('.tmp'+str(os.getpid()));staged.write_bytes(content);os.replace(staged,target)
                else:
                    content=target.read_bytes()
                variants[width,format_]=content
        return original,variants


def generated_assets(app,config,routes,cache=None):
    files={};bindings={}
    origin=config.get('site',{}).get('url','').rstrip('/')
    public=app/'public'
    if origin:
        if not origin.startswith(('https://','http://')) or any(c in origin for c in '\r\n<>"'):
            raise BuildError('[site] url must be an HTTP origin')
        # An application's own public/sitemap.xml or robots.txt takes precedence.
        if not (public/'sitemap.xml').is_file():
            paths=sorted({p for route in routes if route['config'].get('export',True) for p in route['paths']})
            entries=''.join('<url><loc>'+html.escape(origin+path)+'</loc></url>' for path in paths)
            files['/sitemap.xml']=('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+entries+'</urlset>').encode()
        if not (public/'robots.txt').is_file():
            files['/robots.txt']=('User-agent: *\nAllow: /\nSitemap: '+origin+'/sitemap.xml\n').encode()
    images=config.get('images',{})
    if not isinstance(images,dict):raise BuildError('[images] must be a table')
    sources=images.get('files',[])
    if sources:
        try:import PIL  # noqa: F401
        except ImportError as error:raise BuildError('Responsive images require Pillow in the build environment') from error
        widths=images.get('widths',[320,640,1280])
        formats=images.get('formats',['webp'])
        if not isinstance(widths,list) or not widths or any(type(w)!=int or w<1 or w>8192 for w in widths):raise BuildError('Image widths must be integers in 1..8192')
        if not isinstance(formats,list) or not formats or any(f not in ('webp','jpeg','png','avif') for f in formats):raise BuildError('Supported image formats: webp, jpeg, png, avif')
        for source in sources:
            path=(public/source).resolve()
            if not isinstance(source,str) or not path.is_relative_to(public.resolve()) or not path.is_file():raise BuildError(f'Image source must be a public file: {source}')
            original,variants=image_variants(path,widths,formats,cache)
            urls={}
            for (width,format_),content in variants.items():
                url=f'/_web/images/{path.stem}-{width}-{hashlib.sha256(content).hexdigest()[:12]}.{format_}'
                files[url]=content;urls[width,format_]=url
            # Requested widths above the original resolve to the original width.
            for width in widths:
                for format_ in formats:
                    bindings[f'image:{source}:{width}:{format_}']=urls[min(width,original),format_]
            for (width,format_),url in urls.items():
                bindings.setdefault(f'image:{source}:{width}:{format_}',url)
    return files,bindings


def compressible(content_type):
    return content_type.startswith(COMPRESSIBLE)


def compressed(data):
    result=gzip.compress(data,compresslevel=9,mtime=0)
    return result if len(data)>=512 and len(result)<len(data) else None


def brotli_compressed(data,gzipped=None):
    if _brotli is None or len(data)<512:
        return None
    result=_brotli.compress(data,quality=11)
    return result if len(result)<len(gzipped or data) else None
