N = 15
nb = bin(N)[2:]
print(N, nb, nb.count('1') % 2)
nb2 = nb + str(nb.count('1') % 2)
print(nb2)

