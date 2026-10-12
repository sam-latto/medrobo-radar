-- Reference data: plans, communities and the industry catalog.
-- Safe to re-run: updates names and order, never resets an industry's status or profile.

insert into public.plans (slug, name, limits) values
  ('free_ross', 'Free (Ross)',
   '{"find_jobs": {"per": "day", "max": 1}, "tailor_resume": {"per": "month", "max": 30}}')
on conflict (slug) do update set name = excluded.name, limits = excluded.limits;

insert into public.communities (slug, name, email_domain, plan_id)
select 'ross', 'Michigan Ross', 'umich.edu', p.id from public.plans p where p.slug = 'free_ross'
on conflict (slug) do update set name = excluded.name, email_domain = excluded.email_domain;

-- kind 'sector' = a group with sub-industries (following it means following all of them)
-- kind 'industry' = something a pipeline runs for; may have no parent (e.g. Consulting)
insert into public.industries (id, parent_id, name, kind, status, sort_order) values
  ('technology',                  null,               'Technology',                  'sector',   'active', 10),
  ('software_saas',               'technology',       'Software & SaaS',             'industry', 'needs_review', 11),
  ('ai_models_infrastructure',    'technology',       'AI models & infrastructure',  'industry', 'needs_review', 12),
  ('enterprise_ai',               'technology',       'Enterprise AI',               'industry', 'needs_review', 13),
  ('semiconductors',              'technology',       'Semiconductors',              'industry', 'needs_review', 14),
  ('cybersecurity',               'technology',       'Cybersecurity',               'industry', 'needs_review', 15),
  ('cloud_data_infrastructure',   'technology',       'Cloud & data infrastructure', 'industry', 'needs_review', 16),

  ('healthcare',                  null,               'Healthcare',                  'sector',   'active', 20),
  ('health_tech',                 'healthcare',       'Health tech',                 'industry', 'needs_review', 21),
  ('biotech',                     'healthcare',       'Biotech',                     'industry', 'needs_review', 22),
  ('pharmaceuticals',             'healthcare',       'Pharmaceuticals',             'industry', 'needs_review', 23),
  ('medical_devices',             'healthcare',       'Medical devices',             'industry', 'needs_review', 24),
  ('healthcare_robotics',         'healthcare',       'Healthcare robotics',         'industry', 'needs_review', 25),
  ('healthcare_services',         'healthcare',       'Healthcare services',         'industry', 'needs_review', 26),

  ('consulting',                  null,               'Consulting',                  'industry', 'needs_review', 30),

  ('financial_services',          null,               'Financial services',          'sector',   'active', 40),
  ('private_equity',              'financial_services', 'Private equity',            'industry', 'needs_review', 41),
  ('venture_capital',             'financial_services', 'Venture capital',           'industry', 'needs_review', 42),

  ('manufacturing_industrials',   null,               'Manufacturing & industrials', 'sector',   'active', 50),
  ('manufacturing',               'manufacturing_industrials', 'Manufacturing',      'industry', 'needs_review', 51),
  ('advanced_manufacturing',      'manufacturing_industrials', 'Advanced manufacturing', 'industry', 'needs_review', 52),
  ('industrial_automation',       'manufacturing_industrials', 'Industrial automation', 'industry', 'needs_review', 53),

  ('robotics_autonomy',           null,               'Robotics & autonomy',         'sector',   'active', 60),
  ('physical_ai',                 'robotics_autonomy', 'Physical AI & humanoids',    'industry', 'needs_review', 61),
  ('autonomous_vehicles',         'robotics_autonomy', 'Autonomous vehicles',        'industry', 'needs_review', 62),

  ('energy',                      null,               'Energy',                      'sector',   'active', 70),
  ('clean_energy',                'energy',           'Clean energy',                'industry', 'needs_review', 71),
  ('oil_gas',                     'energy',           'Oil & gas',                   'industry', 'needs_review', 72),

  ('consumer_retail',             null,               'Consumer & retail',           'industry', 'needs_review', 80),
  ('marketing_media',             null,               'Marketing & media',           'industry', 'needs_review', 90),
  ('materials_agriculture',       null,               'Materials & agriculture',     'industry', 'needs_review', 100),
  ('real_estate',                 null,               'Real estate',                 'industry', 'needs_review', 110)
on conflict (id) do update set
  parent_id = excluded.parent_id,
  name = excluded.name,
  kind = excluded.kind,
  sort_order = excluded.sort_order;
