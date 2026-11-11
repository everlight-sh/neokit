import {cookies} from 'next/headers';
import {redirect} from 'next/navigation';
import {connection} from 'next/server';
import {session,database} from './security.mjs';
export default async function Page(){
  await connection();
  const user=session((await cookies()).toString());
  if(!user)redirect('/login');
  const tasks=database().tasks.filter(x=>x.owner===user.subject);
  return <main><h1>Your tasks</h1><p>Signed in as {user.subject}</p>
    <form method="post" action="/actions/tasks" data-web-form=""><input type="hidden" name="_csrf" value={user.csrf}/><input type="hidden" name="operation" value="add"/><label>New task<input name="title" minLength="1" maxLength="200" required/></label><button>Add task</button><output data-web-status="" role="status"/></form>
    <ul data-task-list="">{tasks.map(task=><li key={task.id} data-task-id={task.id}><span>{task.title}</span><span>{task.done?'Done':'Open'}</span><form method="post" action="/actions/tasks" data-web-form=""><input type="hidden" name="_csrf" value={user.csrf}/><input type="hidden" name="id" value={task.id}/><button name="operation" value="toggle">Toggle</button><button name="operation" value="delete">Delete</button></form></li>)}</ul>
    <form method="post" action="/logout"><input type="hidden" name="_csrf" value={user.csrf}/><button>Sign out</button></form>
  </main>;
}
