-- Repair the artifact slug recorded on A2A workflow campaigns (NIU-1149).
--
-- Since PR #1012 an A2A launch runs its session as `a2a-<reservation hex>`
-- (the task id, which is also the campaign slug), so `{slug}` in a
-- workflow's declared artifact paths resolves to that value inside the
-- session. The campaign's `a2a_workflow_slug` was instead recorded from the
-- caller's requested session name, so Ting looked for artifacts somewhere the
-- workflow never wrote them and every completed build came back empty.
--
-- `session_name = slug` holds exactly for campaigns launched under the
-- `a2a-<hex>` session name; pre-#1012 campaigns ran under the caller's name,
-- recorded that as their slug correctly, and are left alone. Idempotent.
UPDATE workflow_campaigns
SET metadata = jsonb_set(metadata, '{a2a_workflow_slug}', to_jsonb(slug))
WHERE metadata->>'surface' = 'a2a'
  AND session_name = slug
  AND metadata->>'a2a_workflow_slug' IS DISTINCT FROM slug;
