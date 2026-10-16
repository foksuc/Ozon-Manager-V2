const labels:Record<string,string>={
  VERIFIED_SUCCESS:'Успешно, результат проверен',
  SUCCESS:'Успешно',
  ACCEPTED:'Принято Ozon, проверка части значений недоступна',
  PENDING:'Выполняется',
  UNKNOWN_REPORT_RESULT:'Результат создания отчёта неизвестен',
  OK:'Отчёт готов',
  ERROR:'Ошибка формирования отчёта',
  TIMEOUT:'Время ожидания отчёта истекло',
  CANCEL:'Формирование отменено',
  IN_PROGRESS:'Формируется',
  NOT_STARTED:'В очереди',
  REJECTED:'Изменения отклонены',
  PARTIAL:'Выполнено частично',
  DISCREPANCY:'Обнаружено расхождение',
  UNKNOWN_RESULT:'Результат неизвестен',
}
export const operationStatusText=(status:string)=>labels[status]??'Результат требует проверки'
