from pymongo import MongoClient
client = MongoClient("mongodb+srv://user:user@cluster0.rgocxdb.mongodb.net/cp-admin")
db = client["cp-admin"]
for idx in db.students.list_indexes():
    print(idx['name'], idx.get('unique', False), idx.get('key'))