"""Initialize and compile a locked consumer outside its generated output tree."""
from pathlib import Path
import tempfile
from compiler.build import ROOT,build,compiler_env
from compiler.scaffold import init
from tests.http_smoke import running,request

def main():
    with tempfile.TemporaryDirectory(prefix='neokit-init-') as temporary:
        directory=Path(temporary)
        app=init(directory/'new-app',ROOT,compiler_env())
        assert (app/'neo.lock').is_file()
        source=app/'app/page.neo'
        source.write_text(source.read_text().replace('import "pkg:neokit"','import "pkg:neokit"\nimport "pkg:neokit/auth"\nimport "pkg:neokit/database"').replace('=PageProps {','=let query=pg_select("tasks",arr(i in 0..1) "id",arr(i in 0..0) pg_field("",pg_null()),10) in check query.ok in let session=web_session_verify("","",1) in check !session.authenticated in PageProps {'))
        # Updating a package's own application source is allowed by its lock;
        # only locked dependencies must remain unchanged.
        out,_=build(app,directory/'separate-output',target='server')
        with running(out/'server') as port:
            status,_,body=request(port)
            assert status==200 and b'Hello from NeoKit' in body
        print('PASS scaffold: locked public auth/database package imports and native build into a separate output tree')

if __name__=='__main__':main()
