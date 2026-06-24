import inspect 
from freelancersdk.resources.projects import projects as p 
from freelancersdk.resources.users import users as u 
print('get_users sig',inspect.signature(u.get_users)) 
print(inspect.getsource(u.get_users)) 
print(inspect.getsource(p.search_projects)) 
