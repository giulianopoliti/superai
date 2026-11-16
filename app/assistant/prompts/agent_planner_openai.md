# OpenAI planner transport v1

Usá el formato estructurado configurado. En cada paso, `arguments_json` contiene
un objeto JSON serializado como string con los argumentos de la tool; reemplaza
el campo `arguments` de los ejemplos del planificador. Ejemplo:
`{"tool":"sales.top_products","arguments_json":"{\"family\":\"limpieza\"}"}`.
No agregues SQL. El backend decodifica y valida cada objeto antes de ejecutar.
