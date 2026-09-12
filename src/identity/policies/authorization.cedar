// All resource permissions are tenant-bound, including owner and admin grants.
@id("tenant-isolation")
forbid(principal, action, resource)
when {
    principal.tenant_id == "" ||
    ((resource.tenant_id == "" || principal.tenant_id != resource.tenant_id) &&
     !(resource is Niuu::workflow && resource.scope == "system" &&
       resource.owner_id == "" && resource.tenant_id == "" &&
       [Niuu::Action::"read", Niuu::Action::"list", Niuu::Action::"launch"].contains(action)) &&
     !(resource is Niuu::instance && resource.visibility == "system" && resource.tenant_id == "" &&
       [Niuu::Action::"read", Niuu::Action::"list"].contains(action)))
};

@id("session-admin")
permit(principal, action, resource is Niuu::session)
when { principal.roles.contains("volundr:admin") };

@id("session-owner")
permit(principal, action in [
    Niuu::Action::"read", Niuu::Action::"list", Niuu::Action::"update",
    Niuu::Action::"delete", Niuu::Action::"start", Niuu::Action::"stop",
    Niuu::Action::"report_usage", Niuu::Action::"report_activity", Niuu::Action::"report_timeline",
    Niuu::Action::"report_chronicle", Niuu::Action::"emit_event"
], resource is Niuu::session)
when {
    resource.owner_id == principal.user_id &&
    principal.roles.containsAny(["volundr:developer", "volundr:admin"])
};

@id("session-viewer")
permit(principal, action in [Niuu::Action::"read", Niuu::Action::"list"],
       resource is Niuu::session)
when { principal.roles.contains("volundr:viewer") };

@id("session-create")
permit(principal, action == Niuu::Action::"create", resource is Niuu::session)
when { principal.roles.containsAny(["volundr:developer", "volundr:admin"]) };

@id("secret-tenant-admin")
permit(principal, action, resource is Niuu::secret)
when {
    resource has owner_type && resource.owner_type == "tenant" &&
    resource.owner_id == principal.tenant_id && principal.roles.contains("volundr:admin")
};

@id("secret-owner-read")
permit(principal, action in [Niuu::Action::"read", Niuu::Action::"list"], resource is Niuu::secret)
when {
    (!(resource has owner_type) || (resource has owner_type && resource.owner_type == "user")) &&
    resource.owner_id == principal.user_id &&
    principal.roles.containsAny(["volundr:developer", "volundr:viewer", "volundr:admin"])
};

@id("secret-owner-write")
permit(principal, action in [Niuu::Action::"create", Niuu::Action::"update", Niuu::Action::"delete"],
       resource is Niuu::secret)
when {
    (!(resource has owner_type) || (resource has owner_type && resource.owner_type == "user")) &&
    resource.owner_id == principal.user_id &&
    principal.roles.containsAny(["volundr:developer", "volundr:admin"])
};

@id("pat-owner")
permit(principal, action, resource is Niuu::personal_access_token)
when {
    resource.owner_id == principal.user_id &&
    principal.roles.containsAny(["volundr:developer", "volundr:admin"])
};

@id("saga-owner")
permit(principal, action, resource is Niuu::saga)
when {
    resource.owner_id == principal.user_id &&
    principal.roles.containsAny(["volundr:developer", "volundr:admin"])
};

@id("run-owner")
permit(principal, action, resource is Niuu::run)
when {
    resource.owner_id == principal.user_id &&
    principal.roles.containsAny(["volundr:developer", "volundr:admin"])
};

@id("saga-run-read")
permit(principal, action in [Niuu::Action::"read", Niuu::Action::"list"], resource)
when {
    (resource is Niuu::saga || resource is Niuu::run) &&
    principal.roles.containsAny(["volundr:developer", "volundr:viewer", "volundr:admin"])
};

@id("tenant-admin")
permit(principal, action, resource is Niuu::tenant)
when { principal.roles.contains("volundr:admin") };

@id("tenant-read")
permit(principal, action == Niuu::Action::"read", resource is Niuu::tenant)
when { principal.roles.containsAny(["volundr:developer", "volundr:viewer", "volundr:admin"]) };

// Gateway admission is deliberately separate from resource permissions.
@id("gateway-scoped-credential")
forbid(principal, action == Niuu::Action::"enter", resource is Niuu::gateway)
when {
    resource.scoped && resource.required_scope != "identity:self" &&
    (resource.required_scope == "" || !resource.scopes.contains(resource.required_scope))
};

@id("gateway-admission")
permit(principal, action == Niuu::Action::"enter", resource is Niuu::gateway)
when {
    principal.roles.containsAny(["volundr:developer", "volundr:admin"]) ||
    (principal.roles.contains("volundr:viewer") && ["GET", "HEAD"].contains(resource.method))
};

@id("workflow-read")
permit(principal, action in [Niuu::Action::"read", Niuu::Action::"list", Niuu::Action::"launch"], resource is Niuu::workflow)
when {
    (resource.scope == "system" || resource.owner_id == principal.user_id) &&
    principal.roles.containsAny(["volundr:developer", "volundr:viewer", "volundr:admin"]) &&
    (action != Niuu::Action::"launch" || principal.roles.containsAny(["volundr:developer", "volundr:admin"]))
};

@id("workflow-write")
permit(principal, action in [Niuu::Action::"create", Niuu::Action::"update", Niuu::Action::"delete"], resource is Niuu::workflow)
when {
    (resource.scope == "system" && principal.roles.contains("volundr:admin")) ||
    (resource.scope == "user" && resource.owner_id == principal.user_id &&
     principal.roles.containsAny(["volundr:developer", "volundr:admin"]))
};

@id("campaign-owner")
permit(principal, action, resource is Niuu::campaign)
when {
    resource.owner_id == principal.user_id &&
    principal.roles.containsAny(["volundr:developer", "volundr:admin"])
};

@id("instance-read")
permit(principal, action in [Niuu::Action::"read", Niuu::Action::"list"], resource is Niuu::instance)
when {
    (resource.visibility == "system" || resource.visibility == "tenant" || resource.owner_id == principal.user_id) &&
    principal.roles.containsAny(["volundr:developer", "volundr:viewer", "volundr:admin"])
};

@id("instance-write")
permit(principal, action in [Niuu::Action::"create", Niuu::Action::"update", Niuu::Action::"delete"], resource is Niuu::instance)
when {
    principal.roles.contains("volundr:admin") ||
    (resource.visibility == "user" && resource.owner_id == principal.user_id && principal.roles.contains("volundr:developer"))
};

@id("topology-source-owner")
permit(principal, action, resource is Niuu::topology_source)
when { resource.owner_id == principal.user_id && principal.roles.containsAny(["volundr:developer", "volundr:admin"]) };

@id("topology-source-read")
permit(principal, action in [Niuu::Action::"read", Niuu::Action::"list"], resource is Niuu::topology_source)
when { principal.roles.containsAny(["volundr:developer", "volundr:viewer", "volundr:admin"]) };
