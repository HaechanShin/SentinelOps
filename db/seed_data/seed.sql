INSERT INTO official_responses (issue_tag, content, source) VALUES
('server', 'We are aware of the server issues and our team is actively working on a fix. We will update you as soon as the situation is resolved. Thank you for your patience.', 'PUBG Support'),
('server', 'Server maintenance is currently underway. Expected downtime is approximately 2 hours. Please check our official channels for updates.', 'PUBG Support'),
('bug', 'Thank you for reporting this issue. Our QA team has been able to reproduce the bug and it has been logged for a fix in an upcoming patch.', 'PUBG Support'),
('bug', 'We appreciate you bringing this to our attention. This is a known issue that we are prioritizing for the next hotfix.', 'PUBG Support'),
('cheat', 'We take cheating very seriously. Our anti-cheat team continuously monitors and takes action against players who violate our terms of service. If you encounter a cheater, please use the in-game report system.', 'PUBG Support'),
('cheat', 'Our anti-cheat systems are being constantly improved. We have recently banned over 100,000 accounts in the last wave. Fair play is our top priority.', 'PUBG Support'),
('update', 'Thank you for your feedback on the latest update. We are reviewing community reactions and will consider adjustments in future patches.', 'PUBG Support'),
('ban', 'If you believe your ban was issued in error, please submit an appeal through our official support portal. Each case is reviewed individually by our team.', 'PUBG Support'),
('performance', 'We are aware of the performance issues some players are experiencing. Our engineering team is investigating and optimizing. Please ensure your drivers are up to date.', 'PUBG Support')
ON CONFLICT DO NOTHING;

INSERT INTO patch_notes (version, title, content, published_at) VALUES
('31.1', 'Update 31.1 Patch Notes', 'New weapon: P90 SMG added. Map rotation updated. Bug fixes for parachute landing. Performance improvements for low-end systems. Anti-cheat system enhanced.', '2025-05-01T00:00:00Z'),
('31.2', 'Update 31.2 Patch Notes', 'Erangel visual update Phase 2. New vehicle: Mountain bike. Ranked mode season reset. Multiple bug fixes including inventory drag issue. Sound improvements.', '2025-05-15T00:00:00Z'),
('32.1', 'Update 32.1 Patch Notes', 'New 8x8 map Rondo released. Weapon balance adjustments. DBNO system rework. Custom match improvements. Network optimization for EU/NA servers.', '2025-05-25T00:00:00Z')
ON CONFLICT DO NOTHING;
