import { defineStore } from 'pinia'

/** 可切换的值班团队与角色：用于责任覆盖快照的归属与权限过滤。 */
export const TEAM_OPTIONS = ['A班-采集一组', 'B班-采集二组']
export const ROLE_OPTIONS = ['值班管理员', '团队负责人', '值班成员', '只读角色', '外部访客']
/** 只读/无权限角色：只能查看或被直接拒绝，不能改动责任覆盖配置。 */
export const READONLY_ROLES = ['只读角色', '外部访客']

export const useSessionStore = defineStore('session', {
  state: () => ({
    operator: '值班管理员',
    shiftLabel: '白班 08:00-20:00',
    scope: '多源数据采集与RPA自动化平台',
    team: 'B班-采集二组',
    role: '值班管理员',
  }),
  getters: {
    canOperate: (state) => state.operator.length > 0,
    /** 责任覆盖操作权限：只读角色与外部访客不能改动。 */
    canManageCoverage: (state) => !READONLY_ROLES.includes(state.role),
    /** 责任覆盖查看权限：外部访客直接拒绝。 */
    canViewCoverage: (state) => state.role !== '外部访客',
  },
  actions: {
    setShift(label: string) {
      this.shiftLabel = label
    },
    setTeam(team: string) {
      this.team = team
    },
    setRole(role: string) {
      this.role = role
      if (READONLY_ROLES.includes(role)) {
        this.operator = role
      } else {
        this.operator = '值班管理员'
      }
    },
  },
})
