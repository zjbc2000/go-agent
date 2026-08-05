-- Dev/E2E seed: one auth user, its profile, and a chat session owned by that user.
--
-- Runs after migrations on `supabase db reset` (see [db.seed] in config.toml).
-- The bcrypt hash below is for the password `password123`.

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password,
  email_confirmed_at, confirmation_token, recovery_token,
  email_change, email_change_token_new, email_change_token_current,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at
) values (
  '00000000-0000-0000-0000-000000000000',
  '11111111-1111-4111-8111-111111111111',
  'authenticated',
  'authenticated',
  'e2e@goudan.app',
  '$2a$10$RyllUV7xJHBot9SrDZrEe.RkN3caklbj54PdjY2X1MBxiAc.yMalS',
  now(),
  '', '',
  '', '', '',
  '{"provider":"email","providers":["email"]}',
  '{"name":"E2E User"}',
  now(), now()
);

insert into auth.identities (
  provider_id, user_id, identity_data, provider, last_sign_in_at, created_at, updated_at
) values (
  '11111111-1111-4111-8111-111111111111',
  '11111111-1111-4111-8111-111111111111',
  '{"sub":"11111111-1111-4111-8111-111111111111","email":"e2e@goudan.app","email_verified":true}',
  'email',
  now(), now(), now()
);

insert into public.profiles (id, role, display_name)
values ('11111111-1111-4111-8111-111111111111', 'user', 'E2E User');

insert into public.sessions (id, user_id, title)
values ('22222222-2222-4222-8222-222222222222', '11111111-1111-4111-8111-111111111111', 'E2E Session');
