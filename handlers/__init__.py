from handlers import access, admin, fighter, help, history, leaderboard, match, moves, hub, mitb, mycard, profile, titles, rumble, start, tag, tour

routers = [access.router, fighter.router, mycard.router, titles.router, start.router, help.router, match.router, moves.router, profile.router,
           leaderboard.router, history.router, rumble.router, tag.router, mitb.router, tour.router, hub.router, admin.router]
