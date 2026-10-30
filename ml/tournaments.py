subscriptions=[
    ('ATP', 'Wimbledon'),
    ('WTA', 'Us Open')
]

match_tournament = ('ATP', 'Wimbledon')

if match_tournament in subscriptions:
    print('Recomended')
else:
    print('Not recomended')
