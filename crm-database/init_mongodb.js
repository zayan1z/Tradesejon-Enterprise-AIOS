db.leads.createIndex({ email: 1 }, { unique: true, sparse: true });
db.members.createIndex({ discord_user_id: 1 }, { unique: true, sparse: true });
db.audit_logs.createIndex({ timestamp: -1 });
print("Initialized White-Label CRM Indexes.");
