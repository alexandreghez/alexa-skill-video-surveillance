# alexa-skill-video-surveillance
Skill pour afficher une caméra Home Assistant sur un echo en utilisant APL

Création de cette skill car on quand on affiche des images en APL, cela ne coupe pas la musique.
Je récupere donc des images de la camera a intervalle régulier que j'affiche a une fréquence de 2 images par secondes.

Pour fonctionner on précise le jeton longue durée Home assistant et une liste des caméras.

Skin Alexa configuré en Français mais à adapter pour d'autres langues.

N'hésitez pas à adapter le code en fonction de vos besoins (caroussel d'images, vignettes, ...).

Remarques : 
- La skill s'arrete au bout d'un certain time out mais ne rends pas la main sur l'écho. Il faut donc dire quitter.
- Je l'utilise cette skil dans un scénario home assitant avec Alexandre Media Player ou je lance la skill, attends 30 secondes puis dit "quitter" à la ksill.
- La skill fait des appels lambdas, donc faire attention à ne pas trop en faire même si la limite gratuite de Amazon est élevée
