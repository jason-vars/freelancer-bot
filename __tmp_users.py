from freelancersdk.resources.users import users as u 
print([x for x in dir(u) if 'detail' in x.lower() or 'reputation' in x.lower() or 'status' in x.lower()]) 
