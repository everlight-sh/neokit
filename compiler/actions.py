"""Generate checked Neo adapters from explicit action input schemas."""
from .templates import BuildError, NAME, literal


def action_adapter(config,key):
    schema=config.get('input')
    if schema is None:return None
    if not isinstance(schema,dict) or not NAME.fullmatch(config.get('input_type','')):
        raise BuildError('Typed actions require input_type and an [input] field schema')
    bindings=[];checks=[];values=[]
    for name,description in schema.items():
        if not NAME.fullmatch(name):raise BuildError('Action field names must be Neo identifiers')
        field={'type':description} if isinstance(description,str) else description
        if not isinstance(field,dict) or field.get('type') not in ('text','int','bool'):
            raise BuildError(f'Unsupported action field type: {name}')
        required=field.get('required',True)
        if not isinstance(required,bool):raise BuildError('Action required must be a boolean')
        kind=field['type'];call=f'web_input_{kind}(input,{literal(name)},'
        if kind!='bool':
            low=field.get('min',1 if kind=='text' else -9223372036854775808)
            high=field.get('max',4096 if kind=='text' else 9223372036854775807)
            if type(low)!=int or type(high)!=int or low>high or kind=='text' and (low<0 or high>1048576):raise BuildError(f'Invalid bounds for {name}')
            call+=f'{low},{high},'
        bindings.append(f'let field_{name}={call}{str(required).lower()}) in ')
        checks.append('field_'+name+'.ok');values.append(name+'=field_'+name+'.value')
    csrf=config.get('csrf',True)
    if not isinstance(csrf,bool):raise BuildError('Typed action csrf must be a boolean')
    guard='if !web_same_origin(request,env_get("NEOKIT_ORIGIN").value) then web_reply(403,"Invalid action origin","text/plain") else ' if csrf else ''
    body=(f'effect fn {key}(request:WebRequest) -> WebResponse = {guard}'
        'let input=web_input(request) in if !input.ok then web_status(web_json("{\\"error\\":\\"Invalid request body\\"}"),400) else '
        +''.join(bindings)+'if !('+' && '.join(checks or ['true'])+') then web_status(web_json("{\\"error\\":\\"Invalid action fields\\"}"),422) else '
        +config['handler']+'(request,'+config['input_type']+' {'+','.join(values)+'})')
    return body
